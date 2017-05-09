import hashlib
import json
import mimetypes
from collections import OrderedDict

from django.http import Http404, HttpResponse, HttpResponseNotModified
from rest_framework.decorators import detail_route, list_route
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet, ViewSet

from c3nav.access.apply import filter_arealocations_by_access, filter_queryset_by_access
from c3nav.mapdata.lastupdate import get_last_mapdata_update
from c3nav.mapdata.models import AreaLocation, LocationGroup, Source
from c3nav.mapdata.models.geometry.base import GEOMETRY_MODELS
from c3nav.mapdata.models.geometry.space import Stair
from c3nav.mapdata.models.section import Section
from c3nav.mapdata.search import get_location
from c3nav.mapdata.serializers.main import SectionSerializer, SourceSerializer
from c3nav.mapdata.utils.cache import CachedReadOnlyViewSetMixin, cache_mapdata_api_response, get_bssid_areas_cached


class GeometryTypeViewSet(ViewSet):
    """
    Lists all geometry types.
    """
    @cache_mapdata_api_response()
    def list(self, request):
        return Response([
            OrderedDict((
                ('name', name),
                ('title', str(mapitemtype._meta.verbose_name)),
                ('title_plural', str(mapitemtype._meta.verbose_name_plural)),
            )) for name, mapitemtype in GEOMETRY_MODELS.items()
        ])


class GeometryViewSet(ViewSet):
    """
    List all geometries.
    """
    def list(self, request):
        types = set(request.GET.getlist('type'))
        valid_types = list(GEOMETRY_MODELS.keys())
        if not types:
            types = valid_types
        else:
            types = [t for t in valid_types if t in types]

        cache_key = '__'.join((
            ','.join([str(i) for i in types]),
        ))

        return self._list(request, types=types, add_cache_key=cache_key)

    @staticmethod
    def compare_by_location_type(x: AreaLocation, y: AreaLocation):
        return AreaLocation.LOCATION_TYPES.index(x.location_type) - AreaLocation.LOCATION_TYPES.index(y.location_type)

    @cache_mapdata_api_response()
    def _list(self, request, types):
        results = []
        for t in types:
            mapitemtype = GEOMETRY_MODELS[t]
            queryset = mapitemtype.objects.all()
            queryset = filter_queryset_by_access(request, queryset)
            queryset = queryset.order_by('id')

            if issubclass(mapitemtype, AreaLocation):
                queryset = sorted(queryset, key=AreaLocation.get_sort_key)

            if issubclass(mapitemtype, Stair):
                results.extend(obj.to_shadow_geojson() for obj in queryset)

            results.extend(obj.to_geojson() for obj in queryset)

        return Response(results)


class SectionViewSet(CachedReadOnlyViewSetMixin, ReadOnlyModelViewSet):
    """
    List and retrieve sections.
    """
    queryset = Section.objects.all()
    serializer_class = SectionSerializer
    lookup_field = 'id'


class SourceViewSet(CachedReadOnlyViewSetMixin, ReadOnlyModelViewSet):
    """
    List and retrieve source images (to use as a drafts).
    """
    queryset = Source.objects.all()
    serializer_class = SourceSerializer
    lookup_field = 'id'

    def get_queryset(self):
        return filter_queryset_by_access(self.request, super().get_queryset().all())

    @detail_route(methods=['get'])
    def image(self, request, name=None):
        return self._image(request, name=name, add_cache_key=self._get_add_cache_key(request))

    @cache_mapdata_api_response()
    def _image(self, request, name=None):
        source = self.get_object()
        response = HttpResponse(content_type=mimetypes.guess_type(source.name)[0])
        response.write(source.image)
        return response


class LocationViewSet(ViewSet):
    """
    List and retrieve locations
    """
    # We don't cache this, because it depends on access_list
    lookup_field = 'location_id'

    @staticmethod
    def _filter(queryset):
        return queryset.filter(can_search=True).order_by('id')

    def list(self, request, **kwargs):
        etag = hashlib.sha256(json.dumps({
            'full_access': request.c3nav_full_access,
            'access_list': request.c3nav_access_list,
            'last_update': get_last_mapdata_update().isoformat()
        }).encode()).hexdigest()

        if_none_match = request.META.get('HTTP_IF_NONE_MATCH')
        if if_none_match:
            if if_none_match == etag:
                return HttpResponseNotModified()

        locations = []
        locations += list(filter_queryset_by_access(request, self._filter(LocationGroup.objects.all())))
        locations += sorted(filter_arealocations_by_access(request, self._filter(AreaLocation.objects.all())),
                            key=AreaLocation.get_sort_key, reverse=True)

        response = Response([location.to_location_json() for location in locations])
        response['ETag'] = etag
        response['Cache-Control'] = 'no-cache'
        return response

    def retrieve(self, request, location_id=None, **kwargs):
        location = get_location(request, location_id)
        if location is None:
            raise Http404
        return Response(location.to_location_json())

    @list_route(methods=['POST'])
    def wifilocate(self, request):
        stations = json.loads(request.POST['stations'])[:200]
        if not stations:
            return Response({'location': None})

        bssids = get_bssid_areas_cached()
        stations = sorted(stations, key=lambda l: l['level'])
        for station in stations:
            area_name = bssids.get(station['bssid'].lower())
            if area_name is not None:
                location = get_location(request, area_name)
                if location is not None:
                    return Response({'location': location.to_location_json()})

        return Response({'location': None})
