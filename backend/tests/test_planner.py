from floodline.agents.planner import plan


def test_bridge_report_closes_v31():
    call = plan("The Paiporta bridge on V-31 is under water")
    assert call.tool == "close_road"
    assert call.args["road_id"] == "v31-paiporta-bridge"


def test_coordinate_report_snaps():
    call = plan("Road flooded at 39.4300, -0.4175 and not passable")
    assert call.tool == "close_nearest"
    assert call.args["lat"] == 39.4300
    assert call.args["lon"] == -0.4175


def test_unrelated_text_is_a_noop():
    call = plan("How many beds are free at Peset?")
    assert call.tool == "noop"


def test_disaster_chip_text_still_uses_the_planner():
    call = plan("Waterlogging at 19.1663, 72.8526. The road is under water and not passable.")
    assert call.tool == "close_nearest"
    assert call.args["lat"] == 19.1663
    assert call.args["lon"] == 72.8526
    landslide = plan("Landslide at 19.1700, 72.8500. The road is blocked and not passable.")
    assert landslide.tool == "close_nearest"
