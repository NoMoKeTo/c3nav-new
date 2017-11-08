import operator
import pickle
import threading
from collections import namedtuple
from functools import reduce
from itertools import chain

import numpy as np
from django.db import transaction
from django.utils.functional import cached_property
from shapely.geometry import GeometryCollection, LineString, MultiLineString
from shapely.ops import unary_union

from c3nav.mapdata.cache import MapHistory
from c3nav.mapdata.models import Level, MapUpdate
from c3nav.mapdata.utils.geometry import get_rings
from c3nav.mapdata.utils.mesh import triangulate_rings
from c3nav.mapdata.utils.mpl import shapely_to_mpl


def hybrid_union(geoms):
    if not geoms:
        return HybridGeometry(GeometryCollection(), set())
    if len(geoms) == 1:
        return geoms[0]
    return HybridGeometry(unary_union(tuple(geom.geom for geom in geoms)),
                          reduce(operator.or_, (geom.faces for geom in geoms), set()))


class HybridGeometry(namedtuple('HybridGeometry', ('geom', 'faces'))):
    @classmethod
    def create(cls, geom, face_centers):
        if isinstance(geom, (LineString, MultiLineString)):
            return HybridGeometry(geom, set())
        return HybridGeometry(geom, set(np.argwhere(shapely_to_mpl(geom).contains_points(face_centers)).flatten()))

    def union(self, geom):
        return HybridGeometry(self.geom.union(geom.geom), self.faces | geom.faces)

    def difference(self, geom):
        return HybridGeometry(self.geom.difference(geom.geom), self.faces - geom.faces)

    @cached_property
    def is_empty(self):
        return not self.faces


class AltitudeAreaGeometries:
    def __init__(self, altitudearea=None, colors=None):
        if altitudearea is not None:
            self.geometry = altitudearea.geometry
            self.altitude = altitudearea.altitude
        else:
            self.geometry = None
            self.altitude = None
        self.colors = colors

    def get_geometries(self):
        return chain((self.geometry,), chain(*(areas.values() for areas in self.colors.values())))

    def create_hybrid_geometries(self, face_centers):
        self.geometry = HybridGeometry.create(self.geometry, face_centers)
        self.colors = {color: {key: HybridGeometry.create(geom, face_centers) for key, geom in areas.items()}
                       for color, areas in self.colors.items()}


class FakeCropper:
    @staticmethod
    def intersection(other):
        return other


class LevelRenderData:
    def __init__(self):
        self.levels = []
        self.access_restriction_affected = None

    @staticmethod
    def rebuild():
        levels = tuple(Level.objects.prefetch_related('altitudeareas', 'buildings', 'doors', 'spaces',
                                                      'spaces__holes', 'spaces__columns', 'spaces__locationgroups'))

        single_level_geoms = {level.pk: LevelGeometries.build_for_level(level) for level in levels}

        for i, level in enumerate(levels):
            if level.on_top_of_id is not None:
                continue

            map_history = MapHistory.open_level(level.pk, 'base')

            sublevels = tuple(sublevel for sublevel in levels
                              if sublevel.on_top_of_id == level.pk or sublevel.base_altitude <= level.base_altitude)

            level_crop_to = {}

            # choose a crop area for each level. non-intermediate levels (not on_top_of) below the one that we are
            # currently rendering will be cropped to only render content that is visible through holes indoors in the
            # levels above them.
            crop_to = None
            primary_level_count = 0
            for sublevel in reversed(sublevels):
                geoms = single_level_geoms[sublevel.pk]

                if geoms.holes is not None:
                    primary_level_count += 1

                # set crop area if we area on the second primary layer from top or below
                level_crop_to[sublevel.pk] = crop_to if primary_level_count > 1 else FakeCropper

                if geoms.holes is not None:
                    if crop_to is None:
                        crop_to = geoms.holes
                    else:
                        crop_to = crop_to.intersection(geoms.holes)

            render_data = LevelRenderData()
            render_data.access_restriction_affected = {}

            for sublevel in sublevels:
                old_geoms = single_level_geoms[sublevel.pk]
                crop_to = level_crop_to[sublevel.pk]

                if crop_to is not FakeCropper:
                    map_history.composite(MapHistory.open_level(sublevel.pk, 'base'), crop_to)

                new_geoms = LevelGeometries()
                new_geoms.doors = crop_to.intersection(old_geoms.doors)
                new_geoms.walls = crop_to.intersection(old_geoms.walls)

                for altitudearea in old_geoms.altitudeareas:
                    new_geometry = crop_to.intersection(altitudearea.geometry)
                    if new_geometry.is_empty:
                        continue

                    new_altitudearea = AltitudeAreaGeometries()
                    new_altitudearea.geometry = new_geometry
                    new_altitudearea.altitude = altitudearea.altitude

                    new_colors = {}
                    for color, areas in altitudearea.colors.items():
                        new_areas = {}
                        for access_restriction, area in areas.items():
                            new_area = new_geometry.intersection(area)
                            if not new_area.is_empty:
                                new_areas[access_restriction] = new_area
                        if new_areas:
                            new_colors[color] = new_areas

                    new_altitudearea.colors = new_colors
                    new_geoms.altitudeareas.append(new_altitudearea)

                if new_geoms.walls.is_empty and not new_geoms.altitudeareas:
                    continue

                new_geoms.affected_area = unary_union((
                    *(altitudearea.geometry for altitudearea in new_geoms.altitudeareas),
                    crop_to.intersection(new_geoms.walls.buffer(1))
                ))

                for access_restriction, area in old_geoms.restricted_spaces_indoors.items():
                    new_area = crop_to.intersection(area)
                    if not new_area.is_empty:
                        render_data.access_restriction_affected.setdefault(access_restriction, []).append(new_area)

                new_geoms.restricted_spaces_indoors = {}
                for access_restriction, area in old_geoms.restricted_spaces_indoors.items():
                    new_area = crop_to.intersection(area)
                    if not new_area.is_empty:
                        new_geoms.restricted_spaces_indoors[access_restriction] = new_area

                new_geoms.restricted_spaces_outdoors = {}
                for access_restriction, area in old_geoms.restricted_spaces_outdoors.items():
                    new_area = crop_to.intersection(area)
                    if not new_area.is_empty:
                        new_geoms.restricted_spaces_outdoors[access_restriction] = new_area

                new_geoms.build_mesh()

                render_data.levels.append((new_geoms, sublevel.default_height))

            render_data.access_restriction_affected = {
                access_restriction: unary_union(areas)
                for access_restriction, areas in render_data.access_restriction_affected.items()
            }

            level.render_data = pickle.dumps(render_data)

            map_history.save(MapHistory.level_filename(level.pk, 'render'))

        with transaction.atomic():
            for level in levels:
                level.save()

    cached = {}
    cache_key = None
    cache_lock = threading.Lock()

    @classmethod
    def get(cls, level):
        with cls.cache_lock:
            cache_key = MapUpdate.current_cache_key()
            level_pk = str(level.pk if isinstance(level, Level) else level)
            if cls.cache_key != cache_key:
                cls.cache_key = cache_key
                cls.cached = {}
            else:
                result = cls.cached.get(level_pk, None)
                if result is not None:
                    return result

            if isinstance(level, Level):
                result = pickle.loads(level.render_data)
            else:
                result = pickle.loads(Level.objects.filter(pk=level).values_list('render_data', flat=True)[0])

            cls.cached[level_pk] = result
            return result


class LevelGeometries:
    def __init__(self):
        self.altitudeareas = []
        self.walls = None
        self.doors = None
        self.holes = None
        self.access_restriction_affected = None
        self.restricted_spaces_indoors = None
        self.restricted_spaces_outdoors = None
        self.affected_area = None

        self.vertices = None
        self.faces = None

    @staticmethod
    def build_for_level(level):
        geoms = LevelGeometries()
        buildings_geom = unary_union([b.geometry for b in level.buildings.all()])

        # remove columns and holes from space areas
        for space in level.spaces.all():
            if space.outside:
                space.geometry = space.geometry.difference(buildings_geom)
            space.geometry = space.geometry.difference(unary_union([c.geometry for c in space.columns.all()]))
            space.holes_geom = unary_union([h.geometry for h in space.holes.all()])
            space.walkable_geom = space.geometry.difference(space.holes_geom)

        spaces_geom = unary_union([s.geometry for s in level.spaces.all()])
        doors_geom = unary_union([d.geometry for d in level.doors.all()])
        walkable_spaces_geom = unary_union([s.walkable_geom for s in level.spaces.all()])
        geoms.doors = doors_geom.difference(walkable_spaces_geom)
        walkable_geom = walkable_spaces_geom.union(geoms.doors)
        if level.on_top_of_id is None:
            geoms.holes = spaces_geom.difference(walkable_geom)

        # keep track which areas are affected by access restrictions
        access_restriction_affected = {}

        # keep track wich spaces to hide
        restricted_spaces_indoors = {}
        restricted_spaces_outdoors = {}

        # ground colors
        colors = {}

        # go through spaces and their areas for access control and ground colors
        for space in level.spaces.all():
            access_restriction = space.access_restriction_id
            if access_restriction is not None:
                access_restriction_affected.setdefault(access_restriction, []).append(space.geometry)
                buffered = space.geometry.buffer(0.01).union(unary_union(
                    tuple(door.geometry for door in level.doors.all() if door.geometry.intersects(space.geometry))
                ).difference(walkable_spaces_geom))
                if buffered.intersects(buildings_geom):
                    restricted_spaces_indoors.setdefault(access_restriction, []).append(
                        buffered.intersection(buildings_geom)
                    )
                if not buffered.within(buildings_geom):
                    restricted_spaces_outdoors.setdefault(access_restriction, []).append(
                        buffered.difference(buildings_geom)
                    )

            colors.setdefault(space.get_color(), {}).setdefault(access_restriction, []).append(space.geometry)

            for area in space.areas.all():
                access_restriction = area.access_restriction_id or space.access_restriction_id
                if access_restriction is not None:
                    access_restriction_affected.setdefault(access_restriction, []).append(area.geometry)
                colors.setdefault(area.get_color(), {}).setdefault(access_restriction, []).append(area.geometry)
        colors.pop(None, None)

        # merge ground colors
        for color, color_group in colors.items():
            for access_restriction, areas in tuple(color_group.items()):
                color_group[access_restriction] = unary_union(areas)

        # add altitudegroup geometries and split ground colors into them
        for altitudearea in level.altitudeareas.all():
            altitudearea_colors = {color: {access_restriction: area.intersection(altitudearea.geometry)
                                           for access_restriction, area in areas.items()
                                           if area.intersects(altitudearea.geometry)}
                                   for color, areas in colors.items()}
            altitudearea_colors = {color: areas for color, areas in altitudearea_colors.items() if areas}
            geoms.altitudeareas.append(AltitudeAreaGeometries(altitudearea, altitudearea_colors))

        # merge access restrictions
        geoms.access_restriction_affected = {access_restriction: unary_union(areas)
                                             for access_restriction, areas in access_restriction_affected.items()}
        geoms.restricted_spaces_indoors = {access_restriction: unary_union(spaces)
                                           for access_restriction, spaces in restricted_spaces_indoors.items()}
        geoms.restricted_spaces_outdoors = {access_restriction: unary_union(spaces)
                                            for access_restriction, spaces in restricted_spaces_outdoors.items()}

        geoms.walls = buildings_geom.difference(spaces_geom).difference(doors_geom)

        return geoms

    def get_geometries(self):
        return chain(chain(*(area.get_geometries() for area in self.altitudeareas)), (self.walls, self.doors,),
                     self.restricted_spaces_indoors.values(), self.restricted_spaces_outdoors.values())

    def create_hybrid_geometries(self, face_centers):
        for area in self.altitudeareas:
            area.create_hybrid_geometries(face_centers)
        self.walls = HybridGeometry.create(self.walls, face_centers)
        self.doors = HybridGeometry.create(self.doors, face_centers)
        self.restricted_spaces_indoors = {key: HybridGeometry.create(geom, face_centers)
                                          for key, geom in self.restricted_spaces_indoors.items()}
        self.restricted_spaces_outdoors = {key: HybridGeometry.create(geom, face_centers)
                                           for key, geom in self.restricted_spaces_outdoors.items()}

    def build_mesh(self):
        rings = tuple(chain(*(get_rings(geom) for geom in self.get_geometries())))
        self.vertices, self.faces = triangulate_rings(rings)
        self.create_hybrid_geometries(face_centers=self.vertices[self.faces].sum(axis=1) / 3)
