# ============================================================
# src/preprocessing/speckle_filter.py
# SAR speckle filtering — server-side in GEE
# ============================================================

import ee
import yaml
from pathlib import Path
from loguru import logger


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ============================================================
# dB <-> linear conversion
# ============================================================

def to_linear(image: ee.Image) -> ee.Image:
    """dB to linear: filtering MUST be done in linear scale."""
    return ee.Image(10).pow(image.divide(10))


def to_db(image: ee.Image) -> ee.Image:
    """Linear back to dB."""
    return image.log10().multiply(10)


# ============================================================
# Filter 1: Boxcar (fast, lower quality)
# ============================================================

def boxcar_filter(image: ee.Image, kernel_size: int = 3) -> ee.Image:
    """Simple mean filter. Fast but blurs edges."""
    linear   = to_linear(image)
    kernel   = ee.Kernel.square(radius=kernel_size // 2, units="pixels")
    filtered = linear.reduceNeighborhood(
        reducer=ee.Reducer.mean(), kernel=kernel
    )
    return to_db(filtered).rename(image.bandNames())


# ============================================================
# Filter 2: Lee filter (adaptive, good balance)
# ============================================================

def lee_filter(image: ee.Image, kernel_size: int = 7) -> ee.Image:
    """
    Lee adaptive filter. Preserves edges better than boxcar.
    Weights the filter based on local vs global variance.
    """
    linear = to_linear(image)
    radius = kernel_size // 2
    kernel = ee.Kernel.square(radius=radius, units="pixels")

    mean     = linear.reduceNeighborhood(ee.Reducer.mean(),     kernel)
    variance = linear.reduceNeighborhood(ee.Reducer.variance(), kernel)

    # ENL (Equivalent Number of Looks) for Sentinel-1 IW ~ 4.9
    enl       = 4.9
    noise_var = mean.pow(2).divide(enl)

    var_signal = variance.subtract(noise_var).max(ee.Image(0))
    weight     = var_signal.divide(variance.max(ee.Image(1e-10)))

    filtered = mean.add(weight.multiply(linear.subtract(mean)))
    return to_db(filtered).rename(image.bandNames())


# ============================================================
# Filter 3: Refined Lee — GEE-compatible implementation
# ============================================================

def refined_lee_filter(image: ee.Image) -> ee.Image:
    """
    Refined Lee speckle filter — best edge preservation.
    GEE-compatible implementation using band selection
    instead of arrayArgmin (not available in Python API).

    For each pixel, picks the most homogeneous 3x3 directional
    window and uses it to compute the filtered value.
    """
    img    = to_linear(image)
    bands  = image.bandNames()

    def filter_band(band_name):
        b = img.select([band_name])

        # 8 directional 3x3 kernels
        kernels = [
            ee.Kernel.fixed(3, 3, [[0,0,0],[1,1,1],[0,0,0]]),   # horizontal
            ee.Kernel.fixed(3, 3, [[0,1,0],[0,1,0],[0,1,0]]),   # vertical
            ee.Kernel.fixed(3, 3, [[1,0,0],[0,1,0],[0,0,1]]),   # diagonal /
            ee.Kernel.fixed(3, 3, [[0,0,1],[0,1,0],[1,0,0]]),   # diagonal \
            ee.Kernel.fixed(3, 3, [[1,1,0],[0,1,0],[0,1,1]]),
            ee.Kernel.fixed(3, 3, [[0,1,1],[0,1,0],[1,1,0]]),
            ee.Kernel.fixed(3, 3, [[0,0,0],[1,1,0],[0,1,1]]),
            ee.Kernel.fixed(3, 3, [[0,0,0],[0,1,1],[1,1,0]]),
        ]

        # Compute variance for each directional window
        variances = [
            b.reduceNeighborhood(ee.Reducer.variance(), k)
            for k in kernels
        ]
        means = [
            b.reduceNeighborhood(ee.Reducer.mean(), k)
            for k in kernels
        ]

        # Stack variances into one multi-band image, pick min per pixel
        var_stack  = ee.ImageCollection(variances).toBands()
        mean_stack = ee.ImageCollection(means).toBands()

        # Find minimum variance band index using iterative comparison
        min_var  = var_stack.select(0)
        best_mean = mean_stack.select(0)

        for i in range(1, len(kernels)):
            current_var  = var_stack.select(i)
            current_mean = mean_stack.select(i)
            is_smaller   = current_var.lt(min_var)
            min_var      = min_var.where(is_smaller, current_var)
            best_mean    = best_mean.where(is_smaller, current_mean)

        # Lee weighting using the best directional window
        enl        = 4.9
        img_var    = b.reduceNeighborhood(ee.Reducer.variance(), ee.Kernel.square(2))
        img_mean   = b.reduceNeighborhood(ee.Reducer.mean(),     ee.Kernel.square(2))
        noise_var  = img_mean.pow(2).divide(enl)
        var_signal = img_var.subtract(noise_var).max(ee.Image(0))
        weight     = var_signal.divide(img_var.max(ee.Image(1e-10)))

        filtered = best_mean.add(weight.multiply(b.subtract(best_mean)))
        return to_db(filtered).rename([band_name])

    # Apply per band (VV, VH)
    band_list   = bands.getInfo()
    filtered_bands = [filter_band(b) for b in band_list]
    result = ee.Image.cat(filtered_bands)

    return result


# ============================================================
# Apply to collection or single composite
# ============================================================

def apply_filter_to_collection(
    collection:  ee.ImageCollection,
    method:      str = "lee",
    kernel_size: int = 7
) -> ee.ImageCollection:
    """
    Apply speckle filter to every image in a collection.
    method: 'boxcar' | 'lee' | 'refined_lee'
    """
    method = method.lower()
    if method == "boxcar":
        fn = lambda img: boxcar_filter(img, kernel_size)
    elif method == "lee":
        fn = lambda img: lee_filter(img, kernel_size)
    elif method == "refined_lee":
        fn = lambda img: refined_lee_filter(img)
    else:
        raise ValueError(f"Unknown method: {method}. Use: boxcar | lee | refined_lee")

    logger.info(f"Applying {method} filter to collection (server-side)...")
    filtered = collection.map(fn)
    logger.success("Speckle filter applied")
    return filtered


def filter_composite(
    composite:   ee.Image,
    method:      str = "lee",
    kernel_size: int = 7
) -> ee.Image:
    """Apply speckle filter to a single composite image."""
    method = method.lower()
    if method == "boxcar":
        return boxcar_filter(composite, kernel_size)
    elif method == "lee":
        return lee_filter(composite, kernel_size)
    elif method == "refined_lee":
        return refined_lee_filter(composite)
    else:
        raise ValueError(f"Unknown method: {method}")


# ============================================================
# Preview before/after in notebook
# ============================================================

def preview_filter_comparison(
    composite: ee.Image,
    aoi:       ee.Geometry,
    method:    str = "lee",
    band:      str = "VV"
) -> "geemap.Map":
    """
    Side-by-side raw vs filtered SAR map in notebook.
    Default method is 'lee' — stable across all GEE versions.
    """
    import geemap

    filtered = filter_composite(composite, method=method)
    vis = {"bands": [band], "min": -25, "max": 0, "palette": ["black", "white"]}

    m = geemap.Map()
    m.centerObject(aoi, zoom=9)
    m.addLayer(composite, vis, f"Raw SAR ({band})")
    m.addLayer(filtered,  vis, f"{method.title()} filtered ({band})")
    m.addLayer(aoi, {}, "AOI")

    logger.info("Comparison map ready — toggle layers to compare")
    return m