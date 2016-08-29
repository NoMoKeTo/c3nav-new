import json
from collections import OrderedDict

from django.db import models
from django.utils.translation import ugettext_lazy as _


class Source(models.Model):
    """
    A map source, images of levels that can be useful as backgrounds for the map editor
    """
    name = models.SlugField(_('source name'), max_length=50, unique=True)
    package = models.ForeignKey('Package', on_delete=models.CASCADE, related_name='sources',
                                verbose_name=_('map package'))

    bottom = models.DecimalField(_('bottom coordinate'), max_digits=6, decimal_places=2)
    left = models.DecimalField(_('left coordinate'), max_digits=6, decimal_places=2)
    top = models.DecimalField(_('top coordinate'), max_digits=6, decimal_places=2)
    right = models.DecimalField(_('right coordinate'), max_digits=6, decimal_places=2)

    @classmethod
    def max_bounds(cls):
        result = cls.objects.all().aggregate(models.Min('bottom'), models.Min('left'),
                                             models.Max('top'), models.Max('right'))
        return ((float(result['bottom__min']), float(result['left__min'])),
                (float(result['top__max']), float(result['right__max'])))

    @property
    def bounds(self):
        return ((self.bottom, self.left), (self.top, self.right))

    @property
    def jsbounds(self):
        return json.dumps(((float(self.bottom), float(self.left)), (float(self.top), float(self.right))))

    @classmethod
    def fromfile(cls, data, package, name):
        kwargs = {
            'package': package,
            'name': name,
        }

        if 'bounds' not in data:
            raise ValueError('%s.json: missing bounds.' % name)

        bounds = data['bounds']
        if len(bounds) != 2 or len(bounds[0]) != 2 or len(bounds[1]) != 2:
            raise ValueError('pkg.json: Invalid bounds format.')
        if not all(isinstance(i, (float, int)) for i in sum(bounds, [])):
            raise ValueError('pkg.json: All bounds coordinates have to be int or float.')
        if bounds[0][0] >= bounds[1][0] or bounds[0][1] >= bounds[1][1]:
            raise ValueError('pkg.json: bounds: lower coordinate has to be first.')
        (kwargs['bottom'], kwargs['left']), (kwargs['top'], kwargs['right']) = bounds

        return kwargs

    def jsonize(self):
        return OrderedDict((
            ('name', self.name),
            ('src', 'sources/'+self.get_export_filename()),
            ('bounds', ((float(self.bottom), float(self.left)), (float(self.top), float(self.right)))),
        ))
