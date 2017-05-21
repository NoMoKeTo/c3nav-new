(function () {
    if(L.Browser.chrome && !('ontouchstart' in window)) {
        L.Browser.pointer = false;
        L.Browser.touch = false;
    }
}());


editor = {
    options: {
		position: 'bottomright'
	},

    init: function () {
        // Init Map
        editor.map = L.map('map', {
            zoom: 2,
            maxZoom: 10,
            minZoom: 1,
            crs: L.CRS.Simple,
            editable: true,
            closePopupOnClick: false
        });
        editor.map.on('click', function () {
            editor.map.doubleClickZoom.enable();
        });
        window.onbeforeunload = editor._onbeforeunload;

        L.control.scale({imperial: false}).addTo(editor.map);

        $('#show_map').click(function(e) {
            e.preventDefault();
            $('body').addClass('show-map');
        });
        $('#show_details').click(function(e) {
            e.preventDefault();
            $('body').removeClass('show-map');
        });

        editor._section_control = new SectionControl().addTo(editor.map);

        editor.init_geometries();
        editor.get_sources();

        var bounds = [[0.0, 0.0], [240.0, 400.0]];
        editor.map.setMaxBounds(bounds);
        editor.map.fitBounds(bounds, {padding: [30, 50]});
    },
    _onbeforeunload: function(e) {
        if ($('#sidebar').find('[data-onbeforeunload]').length) {
            e.returnValue = true;
        }
    },

    // sources
    sources: {},
    get_sources: function () {
        // load sources
        editor._sources_control = L.control.layers().addTo(editor.map);

        $.getJSON('/api/sources/', function (sources) {
            var source;
            for (var i = 0; i < sources.length; i++) {
                source = sources[i];
                editor.sources[source.name] = source;
                source.layer = L.imageOverlay('/api/sources/' + source.name + '/image/', source.bounds);
                editor._sources_control.addOverlay(source.layer, source.name);
            }
        });
    },

    // sidebar
    get_location_path: function () {
        return window.location.pathname + window.location.search;
    },
    init_sidebar: function() {
        // init the sidebar. sed listeners for form submits and link clicks
        $('#sidebar').find('.content').on('click', 'a[href]', editor._sidebar_link_click)
                                      .on('click', 'button[type=submit]', editor._sidebar_submit_btn_click)
                                      .on('submit', 'form', editor._sidebar_submit);
        var location_path = editor.get_location_path();
        editor._sidebar_loaded();
        history.replaceState({}, '', location_path);
        window.onpopstate = function() {
            editor.sidebar_get(editor.get_location_path());
        };
    },
    sidebar_get: function(location) {
        // load a new page into the sidebar using a GET request
        if ($('#sidebar').find('.content').html() !== '') {
            history.pushState({}, '', location);
        }
        editor._sidebar_unload();
        $.get(location, editor._sidebar_loaded).fail(editor._sidebar_error);
    },
    _sidebar_unload: function() {
        // unload the sidebar. called on sidebar_get and form submit.
        editor._section_control.disable();
        $('#sidebar').addClass('loading').find('.content').html('');
        editor._unhighlight_geometry();
        editor._cancel_editing();
    },
    _sidebar_loaded: function(data) {
        // sidebar was loaded. load the content. check if there are any redirects. call _check_start_editing.
        var content = $('#sidebar').removeClass('loading').find('.content');;
        if (data !== undefined) {
            content.html($(data));
        }

        var redirect = content.find('span[data-redirect]');
        if (redirect.length) {
            editor.sidebar_get(redirect.attr('data-redirect'));
            return;
        }

        var geometry_url = content.find('[data-geometry-url]');
        if (geometry_url.length) {
            geometry_url = geometry_url.attr('data-geometry-url');
            editor.load_geometries(geometry_url);
            $('body').addClass('map-enabled');
            editor._section_control.clearSections();
            var sections = content.find('[data-sections] a');
            if (sections.length) {
                for(var i=0;i<sections.length;i++) {
                    var section = $(sections[i]);
                    editor._section_control.addSection(section.text(), section.attr('href'), section.is('.current'));
                }
                if (sections.length > 1) {
                    editor._section_control.enable();
                } else {
                    editor._section_control.disable();
                }
                editor._section_control.show()
            } else {
                editor._section_control.hide();
            }
        } else {
            $('body').removeClass('map-enabled').removeClass('show-map');
            editor._section_control.hide();
        }

        editor._check_start_editing();
    },
    _sidebar_error: function(data) {
        $('#sidebar').removeClass('loading').find('.content').html('<h3>Error '+data.status+'</h3>'+data.statusText);
        editor._section_control.hide();
    },
    _sidebar_link_click: function(e) {
        // listener for link-clicks in the sidebar.
        e.preventDefault();
        editor.sidebar_get($(this).attr('href'));
    },
    _sidebar_submit_btn_click: function() {
        // listener for submit-button-clicks in the sidebar, so the submit event will know which button submitted.
        $(this).closest('form').data('btn', $(this)).clearQueue().delay(300).queue(function() {
            $(this).data('btn', null);
        });
    },
    _sidebar_submit: function(e) {
        // listener for form submits in the sidebar.
        e.preventDefault();
        var data = $(this).serialize();
        var btn = $(this).data('btn');
        if (btn !== undefined && btn !== null) {
            if ($(btn).is('[name]')) {
                data += '&' + $('<input>').attr('name', $(btn).attr('name')).val($(btn).val()).serialize();
            }
        }
        var action = $(this).attr('action');
        editor._sidebar_unload();
        $.post(action, data, editor._sidebar_loaded).fail(editor._sidebar_error);
    },

    // geometries
    geometrystyles: {},
    _geometries_layer: null,
    _highlight_layer: null,
    _editing_layer: null,
    _geometries: {},
    _geometries_shadows: {},
    _creating: false,
    _editing: null,
    init_geometries: function () {
        // init geometries and edit listeners
        editor._highlight_layer = L.layerGroup().addTo(editor.map);
        editor._editing_layer = L.layerGroup().addTo(editor.map);

        $('#sidebar').find('.content').on('mouseenter', '.itemtable tr[data-name]', editor._hover_mapitem_row)
                                      .on('mouseleave', '.itemtable tr[data-name]', editor._unhighlight_geometry);

        editor.map.on('editable:drawing:commit', editor._done_creating);
        editor.map.on('editable:editing', editor._update_editing);
        editor.map.on('editable:drawing:cancel', editor._canceled_creating);
        editor.map.on('editable:vertex:click', function () {
            editor.map.doubleClickZoom.disable();
        });
        editor.map.on('editable:vertex:ctrlclick editable:vertex:metakeyclick', function (e) {
            e.vertex.continue();
        });

        $.getJSON('/api/editor/geometrystyles/', function(geometrystyles) {
            editor.geometrystyles = geometrystyles;
            editor.init_sidebar();
        });
    },
    load_geometries: function (geometry_url) {
        // load geometries from url
        editor._geometries = {};
        editor._geometries_shadows = {};
        if (editor._geometries_layer !== null) {
            editor.map.removeLayer(editor._geometries_layer);
        }
        $.getJSON(geometry_url, function(geometries) {
            editor._geometries_layer = L.geoJSON(geometries, {
                style: editor._get_geometry_style,
                onEachFeature: editor._register_geojson_feature
            });

            editor._geometries_layer.addTo(editor.map);
            editor._loading_geometry = false;
        });
    },
    _line_draw_geometry_style: function(style) {
        style.stroke = true;
        style.opacity = 0.6;
        style.color = style.fillColor;
        style.weight = 5;
        return style;
    },
    _get_geometry_style: function (feature) {
        // style callback for GeoJSON loader
        var style = editor._get_mapitem_type_style(feature.properties.type);
        if (feature.geometry.type === 'LineString') {
            style = editor._line_draw_geometry_style(style);
        }
        if (feature.properties.color !== undefined) {
            style.fillColor = feature.properties.color;
        }
        return style
    },
    _get_mapitem_type_style: function (mapitem_type) {
        // get styles for a specific mapitem
        var result = {
            stroke: false,
            fillColor: editor.geometrystyles[mapitem_type],
            fillOpacity: 1,
            smoothFactor: 0
        };
        return result;
    },
    _register_geojson_feature: function (feature, layer) {
        // onEachFeature callback for GeoJSON loader – register all needed events
        if (feature.properties.type === 'shadow') {
            /** @namespace feature.properties.original_name */
            /** @namespace feature.properties.original_type */
            editor._geometries_shadows[feature.properties.original_type+'-'+feature.properties.original_name] = layer;
        } else {
            editor._geometries[feature.properties.type+'-'+feature.properties.name] = layer;
        }
        layer.on('mouseover', editor._hover_geometry_layer)
             .on('mouseout', editor._unhighlight_geometry)
             .on('click', editor._click_geometry_layer)
             .on('dblclick', editor._dblclick_geometry_layer)
    },

    // hover and highlight geometries
    _hover_mapitem_row: function () {
        // hover callback for a itemtable row
        editor._highlight_geometry($(this).closest('.itemtable').attr('data-mapitem-type'), $(this).attr('data-name'));
    },
    _hover_geometry_layer: function (e) {
        // hover callback for a geometry layer
        editor._highlight_geometry(e.target.feature.properties.type, e.target.feature.properties.name);
    },
    _click_geometry_layer: function (e) {
        // click callback for a geometry layer – scroll the corresponding itemtable row into view if it exists
        var properties = e.target.feature.properties;
        var row = $('.itemtable[data-mapitem-type='+properties.type+'] tr[data-name="'+properties.name+'"]');
        if (row.length) {
            row[0].scrollIntoView();
        }
    },
    _dblclick_geometry_layer: function (e) {
        // dblclick callback for a geometry layer - edit this feature if the corresponding itemtable row exists
        var properties = e.target.feature.properties;
        var row = $('.itemtable[data-mapitem-type='+properties.type+'] tr[data-name="'+properties.name+'"]');
        if (row.length) {
            row.find('td:last-child a').click();
            editor.map.doubleClickZoom.disable();
        }
    },
    _highlight_geometry: function(mapitem_type, name) {
        // highlight a geometries layer and itemtable row if they both exist
        var pk = mapitem_type+'-'+name;
        editor._unhighlight_geometry();
        var layer = editor._geometries[pk];
        var row = $('.itemtable[data-mapitem-type='+mapitem_type+'] tr[data-name="'+name+'"]');
        if (layer !== undefined && row.length) {
            row.addClass('highlight');
            L.geoJSON(layer.feature, {
                style: function() {
                    return {
                        color: '#FFFFDD',
                        weight: 3,
                        opacity: 0.7,
                        fillOpacity: 0,
                        className: 'c3nav-highlight'
                    };
                }
            }).addTo(editor._highlight_layer);
        }
    },
    _unhighlight_geometry: function() {
        // unhighlight whatever is highlighted currently
        editor._highlight_layer.clearLayers();
        $('.itemtable .highlight').removeClass('highlight');
    },

    // edit and create geometries
    _check_start_editing: function() {
        // called on sidebar load. start editing or creating depending on how the sidebar may require it
        var sidebarcontent = $('#sidebar').find('.content');

        var geometry_field = sidebarcontent.find('input[name=geometry]');
        if (geometry_field.length) {
            var form = geometry_field.closest('form');
            var mapitem_type = form.attr('data-mapitem-type');
            if (geometry_field.val() !== '') {
                // edit existing geometry
                if (form.is('[data-name]')) {
                    var name = form.attr('data-name');
                    var pk = mapitem_type+'-'+name;
                    editor._geometries_layer.removeLayer(editor._geometries[pk]);
                    var shadow = editor._geometries_shadows[pk];
                    if (shadow) {
                        editor._geometries_layer.removeLayer(shadow);
                    }
                }

                editor._editing = L.geoJSON({
                    type: 'Feature',
                    geometry: JSON.parse(geometry_field.val()),
                    properties: {
                        type: mapitem_type
                    }
                }, {
                    style: editor._get_geometry_style
                }).getLayers()[0];
                editor._editing.on('click', editor._click_editing_layer);
                editor._editing.addTo(editor._editing_layer);
                editor._editing.enableEdit();
            } else if (form.is('[data-geomtype]')) {
                // create new geometry
                form.addClass('creation-lock');
                var geomtype = form.attr('data-geomtype');

                var options = editor._get_mapitem_type_style(mapitem_type);
                if (geomtype === 'polygon') {
                    editor.map.editTools.startPolygon(null, options);
                } else if (geomtype === 'polyline') {
                    options = editor._line_draw_geometry_style(options);
                    editor.map.editTools.startPolyline(null, options);
                }
                editor._creating = true;
                $('#id_level').val(editor._level);
                $('#id_levels').find('option[value='+editor._level+']').prop('selected', true);
            }
        }
    },
    _cancel_editing: function() {
        // called on sidebar unload. cancel all editing and creating.
        if (editor._editing !== null) {
            editor._editing_layer.clearLayers();
            editor._editing.disableEdit();
            editor._editing = null;
        }
        if (editor._creating) {
            editor._creating = false;
            editor.map.editTools.stopDrawing();
        }
    },
    _canceled_creating: function (e) {
        // called after we canceled creating so we can remove the temporary layer.
        if (!editor._creating) {
            e.layer.remove();
        }
    },
    _click_editing_layer: function(e) {
        // click callback for a currently edited layer. create a hole on ctrl+click.
        if ((e.originalEvent.ctrlKey || e.originalEvent.metaKey)) {
            if (e.target instanceof L.Polygon) {
                this.editor.newHole(e.latlng);
            }
        }
    },
    _done_creating: function(e) {
        // called when creating is completed (by clicking on the last point). fills in the form and switches to editing.
        if (editor._creating) {
            editor._creating = false;
            editor._editing = e.layer;
            editor._editing.addTo(editor._editing_layer);
            editor._editing.on('click', editor._click_editing_layer);
            editor._update_editing();
            $('#sidebar').find('.content').find('form.creation-lock').removeClass('creation-lock');
            $('#id_name').focus();
        }
    },
    _update_editing: function () {
        // called if the temporary drawing layer changes. if we are in editing mode (not creating), update the form.
        if (editor._editing !== null) {
            $('#id_geometry').val(JSON.stringify(editor._editing.toGeoJSON().geometry));
        }
    }
};


SectionControl = L.Control.extend({
    options: {
		position: 'bottomright'
	},

	onAdd: function () {
		this._container = L.DomUtil.create('div', 'leaflet-control-sections leaflet-bar');
		this._sectionButtons = [];
		this._disabled = true;
		this._expanded = false;
		this.hide();

		if (!L.Browser.android) {
            L.DomEvent.on(this._container, {
                mouseenter: this.expand,
                mouseleave: this.collapse
            }, this);
        }

        if (!L.Browser.touch) {
            L.DomEvent.on(this._container, 'focus', this.expand, this);
        }

        this._map.on('click', this.collapse, this);

		return this._container;
	},

	addSection: function (title, href, current) {
		var link = L.DomUtil.create('a', (current ? 'current' : ''), this._container);
		link.innerHTML = title;
		link.href = href;

		L.DomEvent
		    .on(link, 'mousedown dblclick', L.DomEvent.stopPropagation)
		    .on(link, 'click', this._sectionClick, this);

        this._sectionButtons.push(link);
		return link;
	},

    clearSections: function() {
        for (var i = 0; i < this._sectionButtons.length; i++) {
            L.DomUtil.remove(this._sectionButtons[i]);
        }
        this._sectionButtons = [];
    },

    disable: function () {
        for (var i = 0; i < this._sectionButtons.length; i++) {
            L.DomUtil.addClass(this._sectionButtons[i], 'leaflet-disabled');
        }
        this.collapse();
        this._disabled = true;
    },

    enable: function () {
        for (var i = 0; i < this._sectionButtons.length; i++) {
            L.DomUtil.removeClass(this._sectionButtons[i], 'leaflet-disabled');
        }
        this._disabled = false;
    },

    hide: function () {
        this._container.style.display = 'none';
    },

    show: function () {
        this._container.style.display = '';
    },

    _sectionClick: function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (!this._expanded) {
            this.expand();
        } else if (!this._disabled) {
            $(e.target).addClass('current').siblings().removeClass('current');
            editor.sidebar_get(e.target.href);
            this.collapse();
        }
	},

    expand: function () {
        if (this._disabled) return;
        this._expanded = true;
		L.DomUtil.addClass(this._container, 'leaflet-control-sections-expanded');
		return this;
	},

	collapse: function () {
        this._expanded = false;
		L.DomUtil.removeClass(this._container, 'leaflet-control-sections-expanded');
		return this;
	}
});


if ($('#sidebar').length) {
    editor.init();
}
