/* GStreamer
 * Copyright (C) 1999,2000 Erik Walthinsen <omega@cse.ogi.edu>
 *                    2000 Wim Taymans <wtay@chello.be>
 *                    2005 Wim Taymans <wim@fluendo.com>
 *                    2007 Andy Wingo <wingo at pobox.com>
 *                    2008 Sebastian Dröge <slomo@circular-chaos.org>
 *
 * interleave.c: interleave samples, mostly based on adder
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

#ifndef __GST_INTERLEAVE_H__
#define __GST_INTERLEAVE_H__

#include <gst/gst.h>
#include <gst/base/gstcollectpads.h>

G_BEGIN_DECLS

#define GST_TYPE_INTERLEAVE            (gst_interleave_get_type())
#define GST_INTERLEAVE(obj)            (G_TYPE_CHECK_INSTANCE_CAST((obj),GST_TYPE_INTERLEAVE,GstLALInterleave))
#define GST_INTERLEAVE_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST((klass),GST_TYPE_INTERLEAVE,GstLALInterleaveClass))
#define GST_INTERLEAVE_GET_CLASS(obj) \
        (G_TYPE_INSTANCE_GET_CLASS ((obj),GST_TYPE_INTERLEAVE,GstLALInterleaveClass))
#define GST_IS_INTERLEAVE(obj)         (G_TYPE_CHECK_INSTANCE_TYPE((obj),GST_TYPE_INTERLEAVE))
#define GST_IS_INTERLEAVE_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE((klass),GST_TYPE_INTERLEAVE))

typedef struct _GstLALInterleave GstLALInterleave;
typedef struct _GstLALInterleaveClass GstLALInterleaveClass;

typedef void (*GstLALInterleaveFunc) (gpointer out, gpointer in, guint stride, guint nframes);

struct _GstLALInterleave
{
  GstElement element;

  /*< private >*/
  GstCollectPads *collect;

  gint channels;
  gint padcounter;
  gint rate;
  gint width;

  GValueArray *channel_positions;
  GValueArray *input_channel_positions;
  gboolean channel_positions_from_input;

  GstCaps *sinkcaps;

  GstClockTime timestamp;
  guint64 offset;

  gboolean segment_pending;
  guint64 segment_position;
  gdouble segment_rate;
  GstSegment segment;

  GstPadEventFunction collect_event;

  GstLALInterleaveFunc func;

  GstPad *src;
};

struct _GstLALInterleaveClass
{
  GstElementClass parent_class;
};

GType gst_interleave_get_type (void);

G_END_DECLS

#endif /* __GST_INTERLEAVE_H__ */
