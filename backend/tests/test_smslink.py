from gusanito.smslink import encode

LUSAKA = [(-15.4165, 28.2849, 0.81), (-15.4101, 28.2951, 0.46)]


def test_first_point_absolute_rest_delta():
    assert encode("https://x.app", LUSAKA) == "https://x.app/?p=-154165_282849_81_64_102_46"


def test_fits_in_one_sms():
    points = [(-15.4165 + i * 0.001, 28.2849 + i * 0.001, 0.8) for i in range(12)]
    assert len(encode("https://gusanito.app", points)) <= 160


def test_pwa_decoder_reads_what_encode_writes():
    """Runs decodeSmsPoints from frontend/index.html in Node on an encoded link."""
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text()
    decoder = re.search(r"^function decodeSmsPoints\(p\) \{.*?^\}", html, re.S | re.M).group(0)
    points = [(-14.3123, 31.6011, 0.86), (-14.2981, 31.5702, 0.99), (-14.3433, 31.556, 0.05)]
    query = encode("https://x.app", points).split("?p=")[1]
    out = subprocess.run([node, "-e", f"{decoder}\nconsole.log(JSON.stringify(decodeSmsPoints({json.dumps(query)})))"],
                         capture_output=True, text=True, check=True).stdout
    assert [(p["lat"], p["lng"], p["prob"]) for p in json.loads(out)] == points
    assert subprocess.run([node, "-e", f"{decoder}\nconsole.log(decodeSmsPoints('1_2').length)"],
                          capture_output=True, text=True, check=True).stdout.strip() == "0"
