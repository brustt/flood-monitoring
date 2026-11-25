from flood_monitoring import _env


def test_gdal_data_is_dropped_without_a_system_gdal(monkeypatch, tmp_path):
    """Light install (#43): no gdal-config, no fallback folder; rasterio's wheel uses its own data, so a shell's
    GDAL_DATA (OTB's) must not leak in and none is invented."""
    monkeypatch.setattr(_env.shutil, "which", lambda name: None)
    monkeypatch.setattr(_env, "_FALLBACK_GDAL_DATA", str(tmp_path / "missing"))
    clean = _env.clean_environment({"GDAL_DATA": "/opt/otb/share/gdal", "HOME": "/home/u"})
    assert "GDAL_DATA" not in clean
    assert clean["HOME"] == "/home/u"


def test_gdal_data_uses_the_fallback_folder_when_it_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(_env.shutil, "which", lambda name: None)
    monkeypatch.setattr(_env, "_FALLBACK_GDAL_DATA", str(tmp_path))
    assert _env.clean_environment({})["GDAL_DATA"] == str(tmp_path)
