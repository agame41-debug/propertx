import report.web as web_module


def test_source_label_maps_hvmb_to_marriott():
    assert web_module._source_family("HVMB") == "marriott"
    assert web_module._source_label("HVMB") == "Marriott"


def test_source_label_preserves_existing_airbnb_and_booking_labels():
    assert web_module._source_label("Airbnb") == "Airbnb"
    assert web_module._source_label("Booking.com") == "Booking"


def test_source_label_falls_back_to_original_for_unknown_source():
    assert web_module._source_family("Expedia") == "other"
    assert web_module._source_label("Expedia") == "Expedia"
