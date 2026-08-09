# Tiles

Local TiTiler service for Cloud Optimized GeoTIFFs. The custom path dependency
rejects files outside `NFM_RASTER_ROOT`; this prevents the generic TiTiler URL
parameter from reading arbitrary local files.

The local Compose service mounts `data/rasters` read-only at `/data/rasters`.
