(function () {
    if(L.Browser.chrome && !('ontouchstart' in window)) {
        L.Browser.pointer = false;
        L.Browser.touch = false;
    }
}());

editor = {
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

        L.control.scale({imperial: false}).addTo(editor.map);

        $('#show_map').click(function() {
            $('body').removeClass('controls');
        });
        $('#show_details').click(function() {
            $('body').addClass('controls');
        });

        editor.init_geometries();
        editor.init_sidebar();
        editor.get_sources();
        editor.get_levels();

        bounds = [[0.0, 0.0], [240.0, 400.0]];
        editor.map.setMaxBounds(bounds);
        editor.map.fitBounds(bounds, {padding: [30, 50]});
    },

    // sources
    sources: {},
    get_sources: function () {
        // load sources
        editor._sources_control = L.control.layers().addTo(editor.map);
        $(editor._sources_control._layersLink).text('Sources');

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

    // levels
    levels: {},
    _level: null,
    _loading_geometry: true,
    _level_fake_layers: {},
    get_levels: function () {
        // load levels and set the lowest one afterwards
        $.getJSON('/api/levels/?ordering=-altitude', function (levels) {
            var control = L.control.layers([], [], {
                position: 'bottomright'
            }).addTo(editor.map);
            $(control._layersLink).text('Levels').parent().addClass('leaflet-levels');

            var level, layer;
            for (var i = levels.length -1; i >= 0; i--) {
                level = levels[i];
                layer = L.circle([-200, -200], 0.1);
                layer._c3nav_level = level.name;
                layer.on('add', editor._click_level);
                editor._level_fake_layers[level.name] = layer;
                control.addBaseLayer(layer, level.name);
            }

            editor._loading_geometry = false;
            editor.set_current_level(levels[0].name);
        });
    },
    _click_level: function(e) {
        if (editor._level === null) return;
        var level = e.target._c3nav_level;
        var success = editor.set_current_level(level);
        if (!success) {
            editor._level_fake_layers[level].remove();
            editor._level_fake_layers[editor._level].addTo(editor.map);
        }
    },
    set_current_level: function(level_name) {
        // sets the current level if the sidebar allows it
        if (editor._loading_geometry) return false;
        var level_switch = $('#mapeditcontrols').find('[data-level-switch]');
        if (level_switch.length === 0) return;
        editor._loading_geometry = true;
        if (editor._level !== null) {
            editor._level_fake_layers[editor._level].remove();
        }
        editor._level_fake_layers[level_name].addTo(editor.map);
        editor._level = level_name;
        editor.get_geometries();

        var level_switch_href = level_switch.attr('data-level-switch');
        if (level_switch_href) {
            editor.sidebar_get(level_switch_href.replace('LEVEL', level_name));
        }
        return true;
    },

    // geometries
    _geometries_layer: null,
    _highlight_layer: null,
    _editing_layer: null,
    _get_geometries_next_time: false,
    _geometries: {},
    _geometries_shadows: {},
    _creating: false,
    _editing: null,
    _geometry_types: [],
    _shown_geometry_types: {},
    _geometry_types_control: null,
    init_geometries: function () {
        // init geometries and edit listeners
        editor._highlight_layer = L.layerGroup().addTo(editor.map);
        editor._editing_layer = L.layerGroup().addTo(editor.map);

        $('#mapeditcontrols').on('mouseenter', '.itemtable tr[data-name]', editor._hover_mapitem_row)
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

        editor._get_geometry_types();
    },
    _get_geometry_types: function() {
        editor._geometry_types_control = L.control.layers().addTo(editor.map);
        $(editor._geometry_types_control._layersLink).text('Types');
        $.getJSON('/api/geometrytypes/', function(geometrytypes) {
            var geometrytype, layer;
            for (var i = 0; i < geometrytypes.length; i++) {
                geometrytype = geometrytypes[i];
                layer = L.circle([-200, -200], 0.1);
                layer._c3nav_geometry_type = geometrytype.name;
                layer.on('add', editor._add_geometrytype_layer);
                layer.on('remove', editor._remove_geometrytype_layer);
                layer.addTo(editor.map);
                editor._geometry_types_control.addOverlay(layer, geometrytype.title_plural);
                editor._geometry_types.push(geometrytype.name)
                editor._shown_geometry_types[geometrytype.name] = true;
            }
        });
    },
    _add_geometrytype_layer: function(e) {
        var type = e.target._c3nav_geometry_type;
        if (!editor._shown_geometry_types[type]) {
            if (editor._loading_geometry) {
                e.target.remove();
                return;
            }
            editor._loading_geometry = true;
            editor._shown_geometry_types[type] = true;
            editor.get_geometries();
        }
    },
    _remove_geometrytype_layer: function(e) {
        var type = e.target._c3nav_geometry_type;
        if (editor._shown_geometry_types[type]) {
            if (editor._loading_geometry) {
                e.target.addTo(map);
                return;
            }
            editor._loading_geometry = true;
            editor._shown_geometry_types[type] = false;
            editor.get_geometries();
        }
    },
    get_geometries: function () {
        // reload geometries of current level
        editor._geometries = {};
        editor._geometries_shadows = {};
        if (editor._geometries_layer !== null) {
            editor.map.removeLayer(editor._geometries_layer);
        }
        geometrytypes = '';
        for (var i = 0; i < editor._geometry_types.length; i++) {
            if (editor._shown_geometry_types[editor._geometry_types[i]]) {
                geometrytypes += '&type=' + editor._geometry_types[i];
            }
        }
        $.getJSON('/api/geometries/?level='+String(editor._level)+geometrytypes, function(geometries) {
            editor._geometries_layer = L.geoJSON(geometries, {
                style: editor._get_geometry_style,
                onEachFeature: editor._register_geojson_feature
            });

            editor._geometries_layer.addTo(editor.map);
            editor._loading_geometry = false;
        });
    },
    _geometry_colors: {
        'building': '#333333',
        'room': '#FFFFFF',
        'outside': '#EEFFEE',
        'lineobstacle': '#999999',
        'obstacle': '#999999',
        'door': '#66FF00',
        'hole': '#66CC99',
        'elevatorlevel': '#9EF8FB',
        'levelconnector': '#FFFF00',
        'shadow': '#000000',
        'stair': '#FF0000',
        'arealocation': '#0099FF',
        'escalator': '#FF9900',
        'escalatorslope': '#DD7700',
        'oneway': '#FF00FF',
        'stuffedarea': '#D9A3A3'
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
        if (feature.geometry.type == 'LineString') {
            style = editor._line_draw_geometry_style(style);
        }
        return style
    },
    _get_mapitem_type_style: function (mapitem_type) {
        // get styles for a specific mapitem
        var result = {
            stroke: false,
            fillColor: editor._geometry_colors[mapitem_type],
            fillOpacity: (mapitem_type == 'arealocation') ? 0.2 : 0.6,
            smoothFactor: 0
        };
        if (mapitem_type == 'arealocation') {
            result.fillOpacity = 0.02;
            result.color = result.fillColor;
            result.stroke = true;
            result.weight = 1;
        }
        return result;
    },
    _register_geojson_feature: function (feature, layer) {
        // onEachFeature callback for GeoJSON loader – register all needed events
        if (feature.properties.type == 'shadow') {
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
        var mapeditcontrols = $('#mapeditcontrols');

        var id_name = $('#id_name');
        id_name.focus();
        if (mapeditcontrols.find('[data-new]').length) {
            id_name.select();
        }

        var geometry_field = mapeditcontrols.find('input[name=geometry]');
        if (geometry_field.length) {
            var form = geometry_field.closest('form');
            var mapitem_type = form.attr('data-mapitem-type');
            if (geometry_field.val() != '') {
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
                if (geomtype == 'polygon') {
                    editor.map.editTools.startPolygon(null, options);
                } else if (geomtype == 'polyline') {
                    options = editor._line_draw_geometry_style(options);
                    editor.map.editTools.startPolyline(null, options);
                }
                editor._creating = true;
                $('#id_level').val(editor._level);
                $('#id_levels').find('option[value='+editor._level+']').prop('selected', true);
            }
        } else if (editor._get_geometries_next_time) {
            editor.get_geometries();
            editor._get_geometries_next_time = false;
        }
    },
    _cancel_editing: function() {
        // called on sidebar unload. cancel all editing and creating.
        if (editor._editing !== null) {
            editor._editing_layer.clearLayers();
            editor._editing.disableEdit();
            editor._editing = null;
            editor._get_geometries_next_time = true;
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
            $('#mapeditcontrols').find('form.creation-lock').removeClass('creation-lock');
            $('#id_name').focus();
        }
    },
    _update_editing: function () {
        // called if the temporary drawing layer changes. if we are in editing mode (not creating), update the form.
        if (editor._editing !== null) {
            $('#id_geometry').val(JSON.stringify(editor._editing.toGeoJSON().geometry));
        }
    },

    // sidebar
    sidebar_location: null,
    init_sidebar: function() {
        // init the sidebar. sed listeners for form submits and link clicks
        $('#mapeditcontrols').on('click', 'a[href]', editor._sidebar_link_click)
                             .on('click', 'button[type=submit]', editor._sidebar_submit_btn_click)
                             .on('submit', 'form', editor._sidebar_submit);
    },
    sidebar_get: function(location) {
        // load a new page into the sidebar using a GET request
        editor._sidebar_unload();
        $.get(location, editor._sidebar_loaded);
    },
    _sidebar_unload: function() {
        // unload the sidebar. called on sidebar_get and form submit.
        $('#mapeditcontrols').html('').addClass('loading');
        editor._unhighlight_geometry();
        editor._cancel_editing();
    },
    _sidebar_loaded: function(data) {
        // sidebar was loaded. load the content. check if there are any redirects. call _check_start_editing.
        var content = $(data);
        var mapeditcontrols = $('#mapeditcontrols');
        mapeditcontrols.html(content).removeClass('loading');

        var redirect = mapeditcontrols.find('form[name=redirect]');
        if (redirect.length) {
            redirect.submit();
            return;
        }

        redirect = $('span[data-redirect]');
        if (redirect.length) {
            editor.sidebar_get(redirect.attr('data-redirect').replace('LEVEL', editor._level));
            return;
        }

        editor._check_start_editing();
    },
    _sidebar_link_click: function(e) {
        // listener for link-clicks in the sidebar.
        e.preventDefault();
        if ($(this).is('[data-level-link]')) {
            editor.set_current_level($(this).attr('data-level-link'));
            return;
        }
        var href = $(this).attr('href');
        if ($(this).is('[data-insert-level]')) {
            href = href.replace('LEVEL', editor._level);
        }
        editor.sidebar_get(href);
    },
    _sidebar_submit_btn_click: function() {
        // listener for submit-button-clicks in the sidebar, so the submit event will know which button submitted.
        $(this).closest('form').data('btn', $(this)).clearQueue().delay(300).queue(function() {
            $(this).data('btn', null);
        });
    },
    _sidebar_submit: function(e) {
        // listener for form submits in the sidebar.
        if ($(this).attr('name') == 'redirect') return;
        e.preventDefault();
        var data = $(this).serialize();
        var btn = $(this).data('btn');
        if (btn !== undefined && btn !== null) {
            if ($(btn).is('[name]')) {
                data += '&' + $('<input>').attr('name', $(btn).attr('name')).val($(btn).val()).serialize();
            }
            if ($(btn).is('[data-reload-geometries]')) {
                editor._get_geometries_next_time = true;
            }
        }
        var action = $(this).attr('action');
        editor._sidebar_unload();
        $.post(action, data, editor._sidebar_loaded);
    }
};


if ($('#mapeditcontrols').length) {
    editor.init();
}
