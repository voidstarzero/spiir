/* 
 * GStreamer
 * Copyright (C) 2011 Leo Singer <leo.singer@ligo.org>
 * Copyright (C) 2007 Sebastian Dröge <slomo@circular-chaos.org>
 * Copyright (C) 2006 Stefan Kost <ensonic@users.sf.net>
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

#ifndef __GST_MULTIRATE_FIR_INTERP_H__
#define __GST_MULTIRATE_FIR_INTERP_H__

#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>
#include <gst/base/gstadapter.h>

G_BEGIN_DECLS
#define GST_TYPE_MULTIRATE_FIR_INTERP            (gst_multirate_fir_interp_get_type())
#define GST_MULTIRATE_FIR_INTERP(obj)            (G_TYPE_CHECK_INSTANCE_CAST((obj),GST_TYPE_MULTIRATE_FIR_INTERP,GstMultirateFirInterp))
#define GST_IS_MULTIRATE_FIR_INTERP(obj)         (G_TYPE_CHECK_INSTANCE_TYPE((obj),GST_TYPE_MULTIRATE_FIR_INTERP))
#define GST_MULTIRATE_FIR_INTERP_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST((klass) ,GST_TYPE_MULTIRATE_FIR_INTERP,GstMultirateFirInterpClass))
#define GST_IS_MULTIRATE_FIR_INTERP_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE((klass) ,GST_TYPE_MULTIRATE_FIR_INTERP))
#define GST_MULTIRATE_FIR_INTERP_GET_CLASS(obj)  (G_TYPE_INSTANCE_GET_CLASS((obj) ,GST_TYPE_MULTIRATE_FIR_INTERP,GstMultirateFirInterpClass))
typedef struct _GstMultirateFirInterp GstMultirateFirInterp;
typedef struct _GstMultirateFirInterpClass GstMultirateFirInterpClass;

typedef void (*GstMultirateFirInterpProcessFunc) (GstMultirateFirInterp *, void *, guint);

struct _GstMultirateFirInterp
{
  GstBaseTransform audiofilter;

  double *kernel;
  guint kernel_length;
  guint64 lag;

  /* < private > */
  GstClockTime t0;
  guint64 offset0;
  guint64 samples;
  gboolean needs_timestamp;

  gint inrate, outrate, channels;
  GstAdapter *adapter;
  double *reordered_kernel;
  guint reordered_kernel_length;
  double *kernel_ptr;
  guint upsample_factor;
};

struct _GstMultirateFirInterpClass
{
  GstBaseTransformClass parent;
};

GType gst_multirate_fir_interp_get_type (void);

G_END_DECLS
#endif /* __GST_MULTIRATE_FIR_INTERP_H__ */
