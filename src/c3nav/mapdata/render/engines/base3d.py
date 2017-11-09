from itertools import chain
from typing import Optional

import numpy as np

from c3nav.mapdata.render.data import HybridGeometry
from c3nav.mapdata.render.engines.base import FillAttribs, RenderEngine, StrokeAttribs


class Base3DEngine(RenderEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.vertices = []

        scale_x = self.scale / self.width * 2
        scale_y = self.scale / self.height * 2
        scale_z = (scale_x+scale_y)/2

        self.np_scale = np.array((scale_x, -scale_y, scale_z))
        self.np_offset = np.array((-self.minx * scale_x - 1, self.maxy * scale_y - 1, 0))

    def _append_to_vertices(self, vertices, append=None):
        if append is not None:
            append = np.array(append, dtype=np.float32).flatten()
            vertices = np.dstack((
                vertices,
                append.reshape(1, append.size).repeat(vertices.shape[0]*3, 0).reshape((-1, 3, append.size))
            ))
        return vertices

    def _place_geometry(self, geometry: HybridGeometry, append=None):
        faces = np.vstack(tuple(chain(geometry.faces, *geometry.add_faces.values())))
        vertices = faces * self.np_scale + self.np_offset
        return self._append_to_vertices(vertices, append)
