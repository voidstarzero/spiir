/*
 *
 * Copyright (C) 2020  Qi Chu, Tom Almeida
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 *
 * Element to attach information from theskipped instrument to the trigger table
 */


/*
 * ============================================================================
 *
 *                                  Preamble
 *
 * ============================================================================
 */

/*
 * Stuff from GStreamer
 */

#include <gst/gst.h>

/*
 * Our own stuff
 */

#include <triggerjointer/triggerjointer.h>

/*
 * ============================================================================
 *
 *                             Plugin Entry Point
 *
 * ============================================================================
 */

static gboolean plugin_init(GstPlugin *plugin) {
    struct {
        const gchar *name;
        GType type;
    } * element, elements[] = {
        { "trigger_jointer", GSTLAL_TYPE_TRIGGER_JOINTER},
        { NULL, 0 },
    };

    /*
     * Tell GStreamer about the elements.
     */

    for (element = elements; element->name; element++)
        if (!gst_element_register(plugin, element->name, GST_RANK_NONE,
                                  element->type))
            return FALSE;

    /*
     * Done.
     */

    return TRUE;
}

/*
 * This is the structure that gst-register looks for.
 */

GST_PLUGIN_DEFINE(GST_VERSION_MAJOR,
                  GST_VERSION_MINOR,
                  "gstlalspiir",
                  "elements to handle spiir triggers",
                  plugin_init,
                  PACKAGE_VERSION,
                  "GPL",
                  PACKAGE_NAME,
                  "http://www.lsc-group.phys.uwm.edu/daswg")
