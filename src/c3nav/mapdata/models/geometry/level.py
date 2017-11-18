import logging
from itertools import chain, combinations
from operator import attrgetter, itemgetter

import numpy as np
from django.db import models
from django.db.models import F
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import ugettext_lazy as _
from scipy.sparse.csgraph._shortest_path import dijkstra
from shapely import prepared
from shapely.affinity import scale
from shapely.geometry import JOIN_STYLE, LineString, MultiPolygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from c3nav.mapdata.cache import changed_geometries
from c3nav.mapdata.fields import GeometryField
from c3nav.mapdata.models import Level
from c3nav.mapdata.models.access import AccessRestrictionMixin
from c3nav.mapdata.models.geometry.base import GeometryMixin
from c3nav.mapdata.models.locations import SpecificLocation
from c3nav.mapdata.utils.geometry import (assert_multilinestring, assert_multipolygon, clean_cut_polygon,
                                          cut_polygon_with_line)


class LevelGeometryMixin(GeometryMixin):
    level = models.ForeignKey('mapdata.Level', on_delete=models.CASCADE, verbose_name=_('level'))

    class Meta:
        abstract = True

    def get_geojson_properties(self, *args, instance=None, **kwargs) -> dict:
        result = super().get_geojson_properties(*args, **kwargs)
        result['level'] = self.level_id
        if hasattr(self, 'get_color'):
            color = self.get_color(instance=instance)
            if color:
                result['color'] = color
        if hasattr(self, 'opacity'):
            result['opacity'] = self.opacity
        return result

    def _serialize(self, level=True, **kwargs):
        result = super()._serialize(**kwargs)
        if level:
            result['level'] = self.level_id
        return result

    def details_display(self):
        result = super().details_display()
        result['display'].insert(3, (str(_('Level')), {'slug': self.level.get_slug(), 'title': self.level.title}))
        return result

    @property
    def subtitle(self):
        base_subtitle = super().subtitle
        level = getattr(self, 'level_cache', None)
        if level is not None:
            return format_lazy(_('{category}, {level}'),
                               category=base_subtitle,
                               level=level.title)
        return base_subtitle

    def register_change(self, force=False):
        if force or self.geometry_changed:
            changed_geometries.register(self.level_id, self.geometry if force else self.get_changed_geometry())

    def register_delete(self):
        changed_geometries.register(self.level_id, self.geometry)

    def save(self, *args, **kwargs):
        self.register_change()
        super().save(*args, **kwargs)


class Building(LevelGeometryMixin, models.Model):
    """
    The outline of a building on a specific level
    """
    geometry = GeometryField('polygon')

    class Meta:
        verbose_name = _('Building')
        verbose_name_plural = _('Buildings')
        default_related_name = 'buildings'


class Space(LevelGeometryMixin, SpecificLocation, models.Model):
    """
    An accessible space. Shouldn't overlap with spaces on the same level.
    """
    geometry = GeometryField('polygon')
    height = models.DecimalField(_('height'), max_digits=6, decimal_places=2, null=True, blank=True)
    outside = models.BooleanField(default=False, verbose_name=_('only outside of building'))

    class Meta:
        verbose_name = _('Space')
        verbose_name_plural = _('Spaces')
        default_related_name = 'spaces'

    def _serialize(self, geometry=True, **kwargs):
        result = super()._serialize(geometry=geometry, **kwargs)
        result['outside'] = self.outside
        result['height'] = None if self.height is None else float(str(self.height))
        return result

    def details_display(self):
        result = super().details_display()
        result['display'].extend([
            (str(_('height')), self.height),
            (str(_('outside only')), str(_('yes') if self.outside else _('no'))),
        ])
        result['editor_url'] = reverse('editor.spaces.detail', kwargs={'level': self.level_id, 'pk': self.pk})
        return result


class Door(LevelGeometryMixin, AccessRestrictionMixin, models.Model):
    """
    A connection between two spaces
    """
    geometry = GeometryField('polygon')

    class Meta:
        verbose_name = _('Door')
        verbose_name_plural = _('Doors')
        default_related_name = 'doors'


class AltitudeArea(LevelGeometryMixin, models.Model):
    """
    An altitude area
    """
    geometry = GeometryField('multipolygon')
    altitude = models.DecimalField(_('altitude'), null=False, max_digits=6, decimal_places=2)
    altitude2 = models.DecimalField(_('second altitude'), null=True, max_digits=6, decimal_places=2)
    point1 = GeometryField('point', null=True)
    point2 = GeometryField('point', null=True)

    class Meta:
        verbose_name = _('Altitude Area')
        verbose_name_plural = _('Altitude Areas')
        default_related_name = 'altitudeareas'
        ordering = ('altitude', )

    def get_altitudes(self, points):
        points = np.asanyarray(points).reshape((-1, 2))
        if self.altitude2 is None:
            return np.full((points.shape[0], ), fill_value=float(self.altitude))

        slope = np.array(self.point2) - np.array(self.point1)
        distances = (np.sum(((points - np.array(self.point1)) * slope), axis=1) / (slope ** 2).sum()).clip(0, 1)
        return self.altitude + distances*(self.altitude2-self.altitude)

    @classmethod
    def recalculate(cls):
        # collect location areas
        all_areas = []
        all_ramps = []
        space_areas = {}
        spaces = {}
        levels = Level.objects.prefetch_related('buildings', 'doors', 'spaces', 'spaces__columns',
                                                'spaces__obstacles', 'spaces__lineobstacles', 'spaces__holes',
                                                'spaces__stairs', 'spaces__ramps', 'spaces__altitudemarkers')
        logger = logging.getLogger('c3nav')

        for level in levels:
            areas = []
            ramps = []
            stairs = []

            # collect all accessible areas on this level
            buildings_geom = unary_union(tuple(building.geometry for building in level.buildings.all()))
            for space in level.spaces.all():
                spaces[space.pk] = space
                space.orig_geometry = space.geometry
                if space.outside:
                    space.geometry = space.geometry.difference(buildings_geom)
                space_accessible = space.geometry.difference(
                    unary_union(tuple(c.geometry for c in space.columns.all()) +
                                tuple(o.geometry for o in space.obstacles.all()) +
                                tuple(o.buffered_geometry for o in space.lineobstacles.all()) +
                                tuple(h.geometry for h in space.holes.all()))
                )

                space_ramps = unary_union(tuple(r.geometry for r in space.ramps.all()))
                areas.append(space_accessible.difference(space_ramps))
                for geometry in assert_multipolygon(space_accessible.intersection(space_ramps)):
                    ramp = AltitudeArea(geometry=geometry, level=level)
                    ramp.geometry_prep = prepared.prep(geometry)
                    ramp.space = space.pk
                    ramps.append(ramp)

            areas = MultiPolygon(tuple(orient(polygon) for polygon in assert_multipolygon(
                unary_union(areas+list(door.geometry for door in level.doors.all())))
            ))

            # collect all stairs on this level
            for space in level.spaces.all():
                for stair in space.stairs.all():
                    stairs.extend(assert_multilinestring(
                        stair.geometry.intersection(space.geometry.buffer(0.001, join_style=JOIN_STYLE.mitre))
                    ))

            # divide areas using stairs
            for stair in stairs:
                areas = MultiPolygon(cut_polygon_with_line(areas, stair))

            # create altitudearea objects
            areas = [AltitudeArea(geometry=clean_cut_polygon(area), level=level)
                     for area in assert_multipolygon(areas)]

            # prepare area geometries
            for area in areas:
                area.geometry_prep = prepared.prep(area.geometry)

            # assign spaces to areas
            space_areas.update({space.pk: [] for space in level.spaces.all()})
            for area in areas:
                area.spaces = set()
                area.geometry_prep = prepared.prep(area.geometry)
                for space in level.spaces.all():
                    if area.geometry_prep.intersects(space.geometry):
                        area.spaces.add(space.pk)
                        space_areas[space.pk].append(area)

            # give altitudes to areas
            for space in level.spaces.all():
                for altitudemarker in space.altitudemarkers.all():
                    for area in space_areas[space.pk]:
                        if area.geometry_prep.contains(altitudemarker.geometry):
                            area.altitude = altitudemarker.altitude
                            break
                    else:
                        raise ValueError(space.title)

            # determine altitude area connections
            for area in areas:
                area.connected_to = []
            for area, other_area in combinations(areas, 2):
                if area.geometry_prep.intersects(other_area.geometry):
                    area.connected_to.append(other_area)
                    other_area.connected_to.append(area)

            # determine ramp connections
            for ramp in ramps:
                ramp.connected_to = []
                buffered = ramp.geometry.buffer(0.001)
                for area in areas:
                    if area.geometry_prep.intersects(buffered):
                        intersection = area.geometry.intersection(buffered)
                        ramp.connected_to.append((area, intersection))
                if len(ramp.connected_to) != 2:
                    if len(ramp.connected_to) == 0:
                        logger.warning('Ramp with no connections!')
                    elif len(ramp.connected_to) == 1:
                        logger.warning('Ramp with only one connection!')
                    else:
                        logger.warning('Ramp with more than one connections!')

            # add areas to global areas
            all_areas.extend(areas)
            all_ramps.extend(ramps)

        # for area in all_areas:
        #     area.geometry = clean_geometry(area.geometry)
        # areas = [area for area in all_areas if not area.geometry.is_empty]

        # give temporary ids to all areas
        areas = all_areas
        ramps = all_ramps
        for i, area in enumerate(areas):
            area.tmpid = i
        for area in areas:
            area.connected_to = set(area.tmpid for area in area.connected_to)
        for space in space_areas.keys():
            space_areas[space] = set(area.tmpid for area in space_areas[space])
        areas_without_altitude = set(area.tmpid for area in areas if area.altitude is None)

        # connect levels
        from c3nav.mapdata.models import GraphEdge
        edges = GraphEdge.objects.exclude(from_node__space__level=F('to_node__space__level'))
        edges = edges.select_related('from_node', 'to_node')
        node_areas = {}
        area_connections = {}
        for edge in edges:
            for node in (edge.from_node, edge.to_node):
                if node.pk not in node_areas:
                    tmpid = next(tmpid for tmpid in space_areas[node.space_id]
                                 if areas[tmpid].geometry.contains(node.geometry))
                    node_areas[node.pk] = tmpid
            area_connections.setdefault(node_areas[edge.from_node.pk], set()).add(node_areas[edge.to_node.pk])
            area_connections.setdefault(node_areas[edge.to_node.pk], set()).add(node_areas[edge.from_node.pk])

        del_keys = tuple(tmpid for tmpid in area_connections.keys() if tmpid not in areas_without_altitude)
        for tmpid in del_keys:
            del area_connections[tmpid]

        do_continue = True
        while do_continue:
            do_continue = False
            del_keys = []
            for tmpid in area_connections.keys():
                connections = area_connections[tmpid] - areas_without_altitude
                if connections:
                    area = areas[tmpid]
                    other_area = areas[next(iter(connections))]
                    area.altitude = other_area.altitude
                    areas_without_altitude.remove(tmpid)
                    del_keys.append(tmpid)

            if del_keys:
                do_continue = True
                for tmpid in del_keys:
                    del area_connections[tmpid]

        # interpolate altitudes
        areas_with_altitude = [i for i in range(len(areas)) if i not in areas_without_altitude]
        for i, tmpid in enumerate(areas_with_altitude):
            areas[tmpid].i = i

        csgraph = np.zeros((len(areas), len(areas)), dtype=bool)
        for area in areas:
            for connected_tmpid in area.connected_to:
                csgraph[area.tmpid, connected_tmpid] = True

        repeat = True
        while repeat:
            repeat = False
            distances, predecessors = dijkstra(csgraph, directed=False, return_predecessors=True, unweighted=True)
            relevant_distances = distances[np.array(areas_with_altitude)[:, None], np.array(areas_with_altitude)]
            # noinspection PyTypeChecker
            for from_i, to_i in np.argwhere(np.logical_and(relevant_distances < np.inf, relevant_distances > 1)):
                from_area = areas[areas_with_altitude[from_i]]
                to_area = areas[areas_with_altitude[to_i]]
                if from_area.altitude == to_area.altitude:
                    continue

                path = [to_area.tmpid]
                while path[-1] != from_area.tmpid:
                    path.append(predecessors[from_area.tmpid, path[-1]])

                from_altitude = from_area.altitude
                delta_altitude = (to_area.altitude-from_altitude)/(len(path)-1)

                if set(path[1:-1]).difference(areas_without_altitude):
                    continue

                for i, tmpid in enumerate(reversed(path[1:-1]), start=1):
                    area = areas[tmpid]
                    area.altitude = from_altitude+delta_altitude*i
                    areas_without_altitude.discard(tmpid)
                    area.i = len(areas_with_altitude)
                    areas_with_altitude.append(tmpid)

                for from_tmpid, to_tmpid in zip(path[:-1], path[1:]):
                    csgraph[from_tmpid, to_tmpid] = False
                    csgraph[to_tmpid, from_tmpid] = False

                repeat = True

        # remaining areas: copy altitude from connected areas if any
        repeat = True
        while repeat:
            repeat = False
            for tmpid in tuple(areas_without_altitude):
                area = areas[tmpid]
                connected_with_altitude = area.connected_to-areas_without_altitude
                if connected_with_altitude:
                    area.altitude = areas[next(iter(connected_with_altitude))].altitude
                    areas_without_altitude.discard(tmpid)
                    repeat = True

        # remaining areas which belong to a room that has an altitude somewhere
        for contained_areas in space_areas.values():
            contained_areas_with_altitude = contained_areas - areas_without_altitude
            contained_areas_without_altitude = contained_areas - contained_areas_with_altitude
            if contained_areas_with_altitude and contained_areas_without_altitude:
                altitude_areas = {}
                for tmpid in contained_areas_with_altitude:
                    area = areas[tmpid]
                    altitude_areas.setdefault(area.altitude, []).append(area.geometry)

                for altitude in altitude_areas.keys():
                    altitude_areas[altitude] = unary_union(altitude_areas[altitude])
                for tmpid in contained_areas_without_altitude:
                    area = areas[tmpid]
                    area.altitude = min(altitude_areas.items(), key=lambda aa: aa[1].distance(area.geometry))[0]
                areas_without_altitude.difference_update(contained_areas_without_altitude)

        # last fallback: level base_altitude
        for tmpid in areas_without_altitude:
            area = areas[tmpid]
            area.altitude = area.level.base_altitude

        # prepare per-level operations
        level_areas = {}
        for area in areas:
            level_areas.setdefault(area.level, set()).add(area.tmpid)

        # make sure there is only one altitude area per altitude per level
        for level in levels:
            areas_by_altitude = {}
            for tmpid in level_areas.get(level, []):
                area = areas[tmpid]
                areas_by_altitude.setdefault(area.altitude, []).append(area.geometry)

            level_areas[level] = [AltitudeArea(level=level, geometry=unary_union(geometries), altitude=altitude)
                                  for altitude, geometries in areas_by_altitude.items()]

        # renumber joined areas
        areas = list(chain(*(a for a in level_areas.values())))
        for i, area in enumerate(areas):
            area.tmpid = i

        # finalize ramps
        for ramp in ramps:
            if not ramp.connected_to:
                for area in space_areas[ramp.space]:
                    ramp.altitude = areas[area].altitude
                    break
                else:
                    ramp.altitude = ramp.level.base_altitude
                continue

            if len(ramp.connected_to) == 1:
                ramp.altitude = ramp.connected_to[0][0].altitude
                continue

            if len(ramp.connected_to) > 2:
                ramp.connected_to = sorted(ramp.connected_to, key=lambda item: item[1].area)[-2:]

            ramp.point1 = ramp.connected_to[0][1].centroid
            ramp.point2 = ramp.connected_to[1][1].centroid
            ramp.altitude = ramp.connected_to[0][0].altitude
            ramp.altitude2 = ramp.connected_to[1][0].altitude

            ramp.tmpid = len(areas)
            areas.append(ramp)
            level_areas[ramp.level].append(ramp)

        #
        # now fill in the obstacles and so on
        #
        for level in levels:
            for space in level.spaces.all():
                space.geometry = space.orig_geometry

            buildings_geom = unary_union(tuple(b.geometry for b in level.buildings.all()))
            doors_geom = unary_union(tuple(d.geometry for d in level.doors.all()))
            space_geom = unary_union(tuple((s.geometry if not s.outside else s.geometry.difference(buildings_geom))
                                           for s in level.spaces.all()))
            accessible_area = unary_union((doors_geom, space_geom))
            for space in level.spaces.all():
                accessible_area = accessible_area.difference(space.geometry.intersection(
                    unary_union(tuple(h.geometry for h in space.holes.all()))
                ))

            our_areas = level_areas.get(level, [])
            for area in our_areas:
                area.orig_geometry = area.geometry

            stairs = []
            for space in level.spaces.all():
                geom = space.geometry
                if space.outside:
                    geom = space_geom.difference(buildings_geom)
                remaining_space = unary_union(
                    tuple(c.geometry for c in space.columns.all()) +
                    tuple(o.geometry for o in space.obstacles.all()) +
                    tuple(o.buffered_geometry for o in space.lineobstacles.all())
                ).intersection(geom).difference(
                    unary_union(tuple(h.geometry for h in space.holes.all()))
                )
                if remaining_space.is_empty:
                    continue

                bounds = geom.bounds
                max_len = ((bounds[0] - bounds[2]) ** 2 + (bounds[1] - bounds[3]) ** 2) ** 0.5
                stairs = []
                for stair in space.stairs.all():
                    for substair in assert_multilinestring(stair.geometry):
                        for coord1, coord2 in zip(tuple(substair.coords)[:-1], tuple(substair.coords)[1:]):
                            line = LineString([coord1, coord2])
                            fact = (max_len * 3) / line.length
                            scaled = scale(line, xfact=fact, yfact=fact)
                            stairs.append(scaled)

                remaining_space = MultiPolygon(tuple(
                    orient(polygon) for polygon in assert_multipolygon(remaining_space)
                ))
                for stair in stairs:
                    remaining_space = MultiPolygon(cut_polygon_with_line(remaining_space, stair))

                for polygon in assert_multipolygon(remaining_space):
                    polygon = clean_cut_polygon(polygon)
                    buffered = polygon.buffer(0.001)

                    center = polygon.centroid
                    touches = tuple((area, buffered.intersection(area.orig_geometry).area)
                                    for area in our_areas
                                    if buffered.intersects(area.orig_geometry))
                    if touches:
                        area = max(touches, key=itemgetter(1))[0]
                    else:
                        area = min(our_areas, key=lambda a: a.orig_geometry.distance(center))
                    area.geometry = area.geometry.union(polygon)

        for level in levels:
            level_areas[level] = set(area.tmpid for area in level_areas.get(level, []))

        # save to database
        areas_to_save = set(range(len(areas)))

        all_candidates = AltitudeArea.objects.select_related('level')
        for candidate in all_candidates:
            candidate.area = candidate.geometry.area
        all_candidates = sorted(all_candidates, key=attrgetter('area'), reverse=True)

        num_modified = 0
        num_deleted = 0
        num_created = 0

        for candidate in all_candidates:
            new_area = None
            for tmpid in level_areas.get(candidate.level, set()):
                area = areas[tmpid]
                if area.geometry.almost_equals(candidate.geometry, 1):
                    new_area = area
                    break

            if new_area is None:
                potential_areas = [(tmpid, areas[tmpid].geometry.intersection(candidate.geometry.buffer(0)).area)
                                   for tmpid in level_areas.get(candidate.level, set())]
                potential_areas = [(tmpid, size) for tmpid, size in potential_areas
                                   if candidate.area and size/candidate.area > 0.9]
                if potential_areas:
                    num_modified += 1
                    new_area = areas[max(potential_areas, key=itemgetter(1))[0]]

            if new_area is None:
                candidate.delete()
                num_deleted += 1
                continue

            candidate.geometry = new_area.geometry
            candidate.altitude = new_area.altitude
            candidate.altitude2 = new_area.altitude2
            candidate.point1 = new_area.point1
            candidate.point2 = new_area.point2
            candidate.save()
            areas_to_save.discard(new_area.tmpid)
            level_areas[new_area.level].discard(new_area.tmpid)

        for tmpid in areas_to_save:
            num_created += 1
            areas[tmpid].save()

        logger = logging.getLogger('c3nav')
        logger.info(_('%d altitude areas built.') % len(areas))
        logger.info(_('%d modified, %d deleted, %d created.') % (num_modified, num_deleted, num_created))
