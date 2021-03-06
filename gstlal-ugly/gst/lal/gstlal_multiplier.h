/* GStreamer
 * Copyright (C) 1999,2000 Erik Walthinsen <omega@cse.ogi.edu>
 *                    2000 Wim Taymans <wtay@chello.be>
 *                    2008 Kipp Cannon <kipp.cannon@ligo.org>
 *                    2010 Drew Keppel <drew.keppel@ligo.org>
 *
 * gstlal_multiplier.h: Header for GSTLALMultiplier element
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */


#ifndef __GSTLAL_MULTIPLIER_H__
#define __GSTLAL_MULTIPLIER_H__


#include <gst/gst.h>
#include <gst/base/gstcollectpads.h>


G_BEGIN_DECLS
#define GSTLAL_TYPE_MULTIPLIER            (gstlal_multiplier_get_type())
#define GSTLAL_MULTIPLIER(obj)            (G_TYPE_CHECK_INSTANCE_CAST((obj), GSTLAL_TYPE_MULTIPLIER, GSTLALMultiplier))
#define GSTLAL_IS_MULTIPLIER(obj)         (G_TYPE_CHECK_INSTANCE_TYPE((obj), GSTLAL_TYPE_MULTIPLIER))
#define GSTLAL_MULTIPLIER_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST((klass), GSTLAL_TYPE_MULTIPLIER, GSTLALMultiplierClass))
#define GSTLAL_IS_MULTIPLIER_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE((klass), GSTLAL_TYPE_MULTIPLIER))
#define GSTLAL_MULTIPLIER_GET_CLASS(obj)  (G_TYPE_INSTANCE_GET_CLASS((obj), GSTLAL_TYPE_MULTIPLIER, GSTLALMultiplierClass))


typedef void (*GSTLALMultiplierFunction) (gpointer out, const gpointer in, size_t size);


/**
 * GSTLALMultiplier:
 *
 * The adder object structure.
 */


typedef struct _GSTLALMultiplier {
	GstElement element;

	GstPad *srcpad;
	GstCollectPads *collect;
	/* pad counter, used for creating unique request pads */
	gint padcount;

	/* stream format */
	gint rate;
	guint unit_size; /* = width / 8 * channels */

	/* function to add samples */
	GSTLALMultiplierFunction func;

	/* counters to keep track of timestamps. */
	gboolean synchronous;

	/* sink event handling */
	GstPadEventFunction collect_event;
	gboolean segment_pending;
	GstSegment segment;
	guint64 offset;

	/* src event handling */
	gboolean flush_stop_pending;
} GSTLALMultiplier;


/**
 * GSTLALMultiplierClass:
 *
 * The adder class structure.
 */


typedef struct _GSTLALMultiplierClass {
	GstElementClass parent_class;
} GSTLALMultiplierClass;


GType gstlal_multiplier_get_type(void);


G_END_DECLS


#endif	/* __GSTLAL_MULTIPLIER_H__ */
