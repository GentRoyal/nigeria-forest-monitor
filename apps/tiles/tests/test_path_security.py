from pathlib import Path

import pytest
from fastapi import HTTPException

from apps.tiles.app import main


def test_local_raster_path_rejects_parent_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_root = tmp_path / "rasters"
    raster_root.mkdir()
    monkeypatch.setattr(main, "RASTER_ROOT", raster_root.resolve())

    with pytest.raises(HTTPException) as error:
        main.local_raster_path(str(tmp_path / "outside.tif"))
    assert error.value.status_code == 403
