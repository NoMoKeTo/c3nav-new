from django.conf.urls import url

from c3nav.site.views import main, map_image, qr_code

urlpatterns = [
    url(r'^map/(?P<level>[a-zA-Z0-9-_:]+)/(?P<area>[a-zA-Z0-9-_:]+).png$', map_image, name='site.level_image'),
    url(r'^qr/(?P<location>[a-zA-Z0-9-_:]+).png$', qr_code, name='site.qr'),
    url(r'^l/(?P<location>[a-zA-Z0-9-_:]+)/$', main, name='site.location'),
    url(r'^o/(?P<origin>[a-zA-Z0-9-_:]+)/$', main, name='site.origin'),
    url(r'^d/(?P<destination>[a-zA-Z0-9-_:]+)/$', main, name='site.destination'),
    url(r'^r/(?P<origin>[a-zA-Z0-9-_:]+)/(?P<destination>[a-zA-Z0-9-_:]+)/$', main, name='site.route'),
    url(r'^$', main, name='site.index')
]
