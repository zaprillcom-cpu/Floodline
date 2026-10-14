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
