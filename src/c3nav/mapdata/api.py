import mimetypes
from itertools import chain

from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.translation import ugettext_lazy as _
from rest_framework.decorators import detail_route, list_route
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from c3nav.mapdata.models import Building, Door, Hole, LocationGroup, Source, Space
from c3nav.mapdata.models.geometry.section import SECTION_MODELS
from c3nav.mapdata.models.geometry.space import SPACE_MODELS, Area, LineObstacle, Obstacle, Point, Stair
from c3nav.mapdata.models.locations import LOCATION_MODELS, Location, LocationRedirect, LocationSlug
from c3nav.mapdata.models.section import Section


class MapdataViewSet(ReadOnlyModelViewSet):
    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        geometry = ('geometry' in request.GET)
        if qs.model in SECTION_MODELS and 'section' in request.GET:
            if not request.GET['section'].isdigit():
                raise ValidationError(detail={'detail': _('%s is not an integer.') % 'section'})
            try:
                section = Section.objects.get(pk=request.GET['section'])
            except Section.DoesNotExist:
                raise NotFound(detail=_('section not found.'))
            qs = qs.filter(section=section)
        if qs.model in SPACE_MODELS and 'space' in request.GET:
            if not request.GET['space'].isdigit():
                raise ValidationError(detail={'detail': _('%s is not an integer.') % 'space'})
            try:
                space = Space.objects.get(pk=request.GET['space'])
            except Space.DoesNotExist:
                raise NotFound(detail=_('section not found.'))
            qs = qs.filter(space=space)
        return Response([obj.serialize(geometry=geometry) for obj in qs.order_by('id')])

    def retrieve(self, request, *args, **kwargs):
        return Response(self.get_object().serialize())

    @staticmethod
    def list_types(models_list, **kwargs):
        return Response([
            model.serialize_type(**kwargs) for model in models_list
        ])


class SectionViewSet(MapdataViewSet):
    queryset = Section.objects.all()

    @list_route(methods=['get'])
    def geometrytypes(self, request):
        return self.list_types(SECTION_MODELS)

    @detail_route(methods=['get'])
    def geometries(self, requests, pk=None):
        section = self.get_object()
        results = []
        results.extend(section.buildings.all())
        results.extend(section.holes.all())
        for space in section.spaces.all():
            results.append(space)
        for door in section.doors.all():
            results.append(door)
        return Response([obj.to_geojson() for obj in results])


class BuildingViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?section=<id> to filter by section. """
    queryset = Building.objects.all()


class SpaceViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?section=<id> to filter by section. """
    queryset = Space.objects.all()

    @list_route(methods=['get'])
    def geometrytypes(self, request):
        return self.list_types(SPACE_MODELS)

    @detail_route(methods=['get'])
    def geometries(self, requests, pk=None):
        space = self.get_object()
        results = chain(
            space.stairs.all(),
            space.areas.all(),
            space.obstacles.all(),
            space.lineobstacles.all(),
            space.points.all(),
        )
        return Response([obj.to_geojson() for obj in results])


class DoorViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?section=<id> to filter by section. """
    queryset = Door.objects.all()


class HoleViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?section=<id> to filter by section. """
    queryset = Hole.objects.all()


class AreaViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?space=<id> to filter by space. """
    queryset = Area.objects.all()


class StairViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?space=<id> to filter by space. """
    queryset = Stair.objects.all()


class ObstacleViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?space=<id> to filter by space. """
    queryset = Obstacle.objects.all()


class LineObstacleViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?space=<id> to filter by space. """
    queryset = LineObstacle.objects.all()


class PointViewSet(MapdataViewSet):
    """ Add ?geometry=1 to get geometries, add ?space=<id> to filter by space. """
    queryset = Point.objects.all()


class LocationGroupViewSet(MapdataViewSet):
    queryset = LocationGroup.objects.all()


class LocationViewSet(RetrieveModelMixin, GenericViewSet):
    """ Add ?show_redirect=1 to suppress redirects and show them as JSON. """
    queryset = LocationSlug.objects.all()
    lookup_field = 'slug'

    def retrieve(self, request, slug=None, *args, **kwargs):
        result = Location.get_by_slug(slug, self.get_queryset())
        if result is None:
            raise NotFound
        if isinstance(result, LocationRedirect):
            if 'show_redirects' in request.GET:
                return Response(result.serialize(include_type=True))
            return redirect('../'+result.target.slug)  # todo: why does redirect/reverse not work here?
        return Response(result.get_child().serialize(include_type=True))

    @list_route(methods=['get'])
    def types(self, request):
        return MapdataViewSet.list_types(LOCATION_MODELS, geomtype=False)


class SourceViewSet(MapdataViewSet):
    queryset = Source.objects.all()

    @detail_route(methods=['get'])
    def image(self, request, pk=None):
        return self._image(request, pk=pk)

    def _image(self, request, pk=None):
        source = self.get_object()
        response = HttpResponse(content_type=mimetypes.guess_type(source.name)[0])
        response.write(source.image)
        return response
