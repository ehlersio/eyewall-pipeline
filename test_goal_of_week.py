"""Tests for goal_of_week.py -- no network, no Supabase. Replays are synthetic
frames in the tracking feed's shape (inches on a 200 x 85 ft rink, 10 Hz)."""

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

import goal_of_week as gw
import social_posts as sp

BETTING_WORDS = ("odds", "pick", "bet", "lock", "line", "value", "wager", "spread")


def goal(**overrides):
    g = {
        "game_id": 2025020500,
        "game_date": "2026-01-07",
        "game_type": 2,
        "event_id": 700,
        "period": 3,
        "period_type": "REG",
        "time": "17:50",
        "team": "LAK",
        "opp": "SJS",
        "home": "LAK",
        "away": "SJS",
        "is_home": True,
        "home_score": 3,
        "away_score": 2,
        "diff_before": 0,
        "empty_net": False,
        "player_id": 99,
        "first": "Alex",
        "last": "Laferriere",
        "goals_to_date": 12,
        "assists": ["J. Edmundson", "C. Ceci"],
        "shot_type": "tip-in",
        "strength": "EV",
        "replay_url": "https://example/replay.json",
    }
    g.update(overrides)
    return g


def replay(puck_path_ft, high=True, n_players=2):
    """Frames with the puck at each (a, b) of puck_path_ft (attack coordinates,
    feet) and a couple of skaters, converted back to raw inches."""
    frames = []
    for t, (a, b) in enumerate(puck_path_ft):
        x_ft, y_ft = (a, gw.RINK_W - b) if high else (gw.RINK_L - a, b)
        objs = {"1": {"id": 1, "playerId": "", "x": x_ft * 12, "y": y_ft * 12, "teamAbbrev": ""}}
        for k in range(n_players):
            objs[str(100 + k)] = {
                "id": 100 + k,
                "playerId": 99 if k == 0 else 50 + k,
                "x": x_ft * 12 - 36,
                "y": y_ft * 12,
                "sweaterNumber": 14 + k,
                "teamAbbrev": "LAK" if k % 2 == 0 else "SJS",
            }
        frames.append({"timeStamp": 1000 + t, "onIce": objs})
    return frames


def straight_rush(start_a=100.0, frames_to_goal=80, after=20):
    """Puck skated straight up the middle into the net, then sitting in it."""
    path = []
    for i in range(frames_to_goal + 1):
        a = start_a + (gw.GOAL_LINE + 1 - start_a) * i / frames_to_goal
        path.append((a, gw.NET_CENTER_B))
    path += [(gw.GOAL_LINE + 1, gw.NET_CENTER_B)] * after
    return path


# ── Classification ──────────────────────────────────────────────────────


class TestEmptyNet:
    @pytest.mark.parametrize(
        ("code", "scorer_home", "expected"),
        [
            ("1551", True, False),
            ("0651", True, True),  # away goalie pulled, home scores
            ("1560", False, True),  # home goalie pulled, away scores
            ("1560", True, False),  # home's own net empty doesn't matter to home
            ("", True, False),
        ],
    )
    def test_defending_goalie_digit(self, code, scorer_home, expected):
        assert gw.empty_net(code, scorer_home) is expected


class TestClutchKind:
    def test_overtime_always_counts(self):
        assert gw.clutch_kind(goal(period=4, period_type="OT", time="01:02")) == "ot"

    def test_late_go_ahead_and_tying(self):
        assert gw.clutch_kind(goal(diff_before=0)) == "go-ahead"
        assert gw.clutch_kind(goal(diff_before=-1)) == "tying"

    @pytest.mark.parametrize(
        "g",
        [
            goal(time="14:59"),  # not the last 5 minutes
            goal(period=2),
            goal(diff_before=1),  # extends a lead
            goal(diff_before=-2),  # still behind
            goal(empty_net=True),
            goal(period=4, period_type="OT", empty_net=True),
        ],
    )
    def test_not_clutch(self, g):
        assert gw.clutch_kind(g) is None

    def test_situational_order_ot_then_go_ahead_then_tying_latest_first(self):
        gs = [
            goal(event_id=1, diff_before=-1, time="19:00"),
            goal(event_id=2, diff_before=0, time="16:00"),
            goal(event_id=3, diff_before=0, time="18:30"),
            goal(event_id=4, period=4, period_type="OT", time="00:30"),
        ]
        assert [g["event_id"] for g in sorted(gs, key=gw.situational_order)] == [4, 3, 2, 1]


def landing_payload():
    """A game center `landing` payload: two 3rd-period goals and a shootout."""

    def g(event_id, is_home, hs, aws, code="1551", team="LAK"):
        return {
            "eventId": event_id,
            "isHome": is_home,
            "teamAbbrev": {"default": team},
            "homeScore": hs,
            "awayScore": aws,
            "situationCode": code,
            "timeInPeriod": "18:00",
            "playerId": 9,
            "firstName": {"default": "A"},
            "lastName": {"default": "B"},
            "assists": [{"name": {"default": "C. D"}}],
            "shotType": "wrist",
            "strength": "ev",
            "pptReplayUrl": f"https://x/{event_id}.json",
        }

    return {
        "id": 1,
        "gameDate": "2026-01-07",
        "gameType": 2,
        "homeTeam": {"abbrev": "LAK"},
        "awayTeam": {"abbrev": "SJS"},
        "summary": {
            "scoring": [
                {
                    "periodDescriptor": {"number": 3, "periodType": "REG"},
                    "goals": [g(10, False, 0, 1, team="SJS"), g(11, True, 1, 1)],
                },
                {
                    "periodDescriptor": {"number": 5, "periodType": "SO"},
                    "goals": [g(12, True, 2, 1)],
                },
            ]
        },
    }


class TestGoalsInGame:
    def test_flattens_with_score_before_and_skips_shootout(self):
        goals = gw.goals_in_game(landing_payload())
        assert [(g["event_id"], g["team"], g["opp"], g["diff_before"]) for g in goals] == [
            (10, "SJS", "LAK", 0),
            (11, "LAK", "SJS", -1),
        ]
        assert goals[1]["strength"] == "EV" and goals[1]["assists"] == ["C. D"]


# ── Replay geometry ─────────────────────────────────────────────────────


class TestReplayGeometry:
    @pytest.mark.parametrize("high", [True, False])
    def test_attack_end_goal_frame_and_rush(self, high):
        frames = replay(straight_rush(start_a=100, frames_to_goal=80), high=high)
        assert gw.attack_side_high(frames) is high
        gf = gw.goal_frame(frames, high)
        assert gf == 80  # first frame over the line between the posts
        # 6 s before the goal at a steady pace over 90 ft in 8 s: 67.5 ft
        assert gw.rush_feet(frames, high, gf) == pytest.approx(67.5, abs=0.5)

    def test_goal_frame_falls_back_to_closest_approach(self):
        path = [(150 + i, 30.0) for i in range(30)]  # wide of the net the whole way
        frames = replay(path)
        assert gw.goal_frame(frames, True) == 29

    def test_puck_carried_behind_the_net_is_not_a_goal(self):
        around = [(180.0, 50.0), (188.0, 50.0), (192.0, 48.0), (195.0, 44.0), (195.0, 41.0)]
        into_net = [(185.0, 42.5), (190.0, 42.5)]
        frames = replay(around + into_net)
        assert gw.goal_frame(frames, True) == len(around) + 1

    def test_puck_lost_on_the_shot_is_found_in_the_net(self):
        frames = replay([(170.0, 40.0), (175.0, 41.0), (180.0, 41.5), (194.0, 41.0), (192.0, 41.4)])
        for f in frames[2:3]:  # tracking drops the puck mid-shot
            f["onIce"].pop("1")
        assert gw.goal_frame(frames, True) == 3

    def test_puck_lost_for_a_second_turns_up_noisy_inside_the_net(self):
        # The preseason snap shot: gone for 9 frames, found 6.7 ft deep and
        # 7 ft off centre, then drifting into the middle of the net.
        path = [(164.5, 16.7), (172.1, 16.5)] + [(0.0, 0.0)] * 9 + [(195.7, 35.4), (193.5, 39.0)]
        frames = replay(path)
        for f in frames[2:11]:
            f["onIce"].pop("1")
        assert gw.goal_frame(frames, True) == 11

    def test_a_short_blip_behind_the_goal_line_is_not_a_goal(self):
        path = [(185.0, 50.0), (0.0, 0.0), (194.0, 48.0), (180.0, 42.5), (190.0, 42.5)]
        frames = replay(path)
        frames[1]["onIce"].pop("1")  # one missing frame is not a lost shot
        assert gw.goal_frame(frames, True) == 4

    def test_rush_ignores_half_second_jitter(self):
        still = [(185.0 + (0.3 if i % 2 else 0.0), 42.5) for i in range(70)] + [(190.0, 42.5)]
        frames = replay(still)
        # The real movement is the last 5 ft into the net. Summing every
        # 0.1 s sample would add 60 x 0.3 ft of jitter (~23 ft in all);
        # half-second sampling keeps the total under 10.
        assert 4.7 < gw.rush_feet(frames, True, 70) < 10


# ── The pick ────────────────────────────────────────────────────────────


class TestPickGoal:
    def test_longest_rush_among_clutch_goals(self):
        goals = [
            goal(event_id=1, diff_before=0, replay_url="short"),
            goal(event_id=2, diff_before=-1, replay_url="long"),
            goal(event_id=3, time="05:00", replay_url="longest-but-early"),
        ]
        replays = {
            "short": replay(straight_rush(start_a=170, frames_to_goal=60)),
            "long": replay(straight_rush(start_a=80, frames_to_goal=60)),
            "longest-but-early": replay(straight_rush(start_a=20, frames_to_goal=60)),
        }
        with patch.object(gw, "fetch_replay", side_effect=lambda url, http: replays[url]):
            pick, fallback = gw.pick_goal(goals, http=None)
        assert pick["event_id"] == 2 and fallback is None
        assert pick["rush_ft"] > 100

    def test_no_replays_falls_back_to_the_situational_pick(self):
        goals = [goal(event_id=1, diff_before=-1), goal(event_id=2, period=4, period_type="OT")]
        with patch.object(gw, "fetch_replay", return_value=None):
            pick, fallback = gw.pick_goal(goals, http=None)
        assert pick is None and fallback["event_id"] == 2

    def test_week_without_clutch_goals_ranks_every_non_empty_net_goal(self):
        goals = [goal(event_id=1, period=1), goal(event_id=2, period=1, empty_net=True)]
        with patch.object(gw, "fetch_replay", return_value=replay(straight_rush())):
            pick, _ = gw.pick_goal(goals, http=None)
        assert pick["event_id"] == 1


# ── Presentation ────────────────────────────────────────────────────────


class TestPresentation:
    def test_clashing_team_colours_turn_the_opponent_grey(self):
        assert gw.team_colours("CAR", "DET")["DET"] == gw.NEUTRAL_DOT
        assert gw.team_colours("CAR", "FLA")["FLA"] == sp.team_color("FLA")

    def test_period_labels(self):
        assert gw.fmt_period(goal(period=3)) == "3rd"
        assert gw.fmt_period(goal(period=4, period_type="OT")) == "OT"
        assert gw.fmt_period(goal(period=6, period_type="OT")) == "3OT"

    def test_caption_is_neutral_and_reads_cleanly(self):
        g = {**goal(), "rush_ft": 230.4}
        cap = gw.caption(g, "Jan 5\u201311")
        assert cap.startswith(
            "Goal of the Week, Jan 5\u201311: Alex Laferriere (LAK), a late go-ahead goal "
            "vs the San Jose Sharks on Wed, Jan 7."
        )
        assert "230 ft in the last 6 seconds" in cap
        assert cap.rstrip().endswith("#LosAngelesKings")
        for word in BETTING_WORDS:
            assert word not in cap.lower().replace("online", "")

    def test_caption_for_an_overtime_winner_uses_an(self):
        g = {**goal(period=4, period_type="OT"), "rush_ft": 100}
        assert "an overtime winner" in gw.caption(g, "Jan 5\u201311")

    def test_still_caption_leaves_out_the_replay_lines(self):
        cap = gw.caption(goal(), "Jan 5\u201311", replay=False)
        assert "tracking" not in cap and "ft in the last" not in cap

    def test_clip_frames_sizes_count_and_cover_time(self):
        frames = replay(straight_rush(start_a=100, frames_to_goal=100, after=30))
        g = gw.analyse(goal(), frames)
        clip = list(gw.replay_frames(g))
        shown = (g["goal_frame"] + int(gw.TAIL_SECONDS * gw.TRACK_HZ)) - (
            g["goal_frame"] - gw.LEAD_SECONDS * gw.TRACK_HZ
        )
        assert len(clip) == shown * (gw.FPS // gw.TRACK_HZ) + 1 + int(gw.CARD_SECONDS * gw.FPS)
        assert {f.size for f in clip} == {(gw.VW, gw.VH)}
        assert gw.goal_moment_ms(g) == gw.LEAD_SECONDS * 1000

    def test_still_location_matches_the_replay_orientation(self):
        class Res:
            def raise_for_status(self):
                pass

            def json(self):
                return {"plays": [{"eventId": 700, "details": {"xCoord": 80, "yCoord": 10}}]}

        class Http:
            def get(self, url, timeout=None):
                return Res()

        # Attacking the high-x end: tracking y ft = 42.5 - 10 = 32.5, which
        # to_attack() turns to b = 85 - 32.5 = 52.5.
        assert gw.shot_location(goal(), Http()) == (180.0, 52.5)
        tracked = {"x": (100 + 80) * 12, "y": (42.5 - 10) * 12}
        assert gw.to_attack(tracked, True) == pytest.approx((180.0, 52.5))

    def test_still_card_renders(self):
        img = gw.render_still(goal(), (180.0, 40.0))
        assert img.size == (sp.W, sp.H)


# ── Encoding ────────────────────────────────────────────────────────────


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None and not os.environ.get("CI"),
    reason="ffmpeg not installed locally (CI installs it, so it runs there)",
)


@needs_ffmpeg
def test_encode_mp4_meets_reels_specs():
    frames = replay(straight_rush(start_a=160, frames_to_goal=20, after=15))
    g = gw.analyse(goal(), frames)
    mp4 = gw.encode_mp4(gw.replay_frames(g))
    probe_path = "probe.mp4"
    try:
        with open(probe_path, "wb") as f:
            f.write(mp4)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", probe_path],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    finally:
        os.remove(probe_path)
    streams = {s["codec_type"]: s for s in json.loads(out)["streams"]}
    video, audio = streams["video"], streams["audio"]
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1080, 1920)
    assert video["pix_fmt"] == "yuv420p" and video["r_frame_rate"] == "30/1"
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) <= 48000


# ── Landscape layout ────────────────────────────────────────────────────


class TestLandscapeGeometry:
    def test_turned_puts_the_attacked_net_on_the_right(self):
        # A marker at (a=180, b=20) on a portrait image (attacked net at the
        # top, b across from the left) lands at x = a, y = b once turned.
        s = 2
        img = Image.new("L", (int(gw.RINK_W * s), int(gw.RINK_L * s)), 0)
        img.putpixel((20 * s, int((gw.RINK_L - 180) * s)), 255)
        out = gw.turned(img)
        assert out.size == (img.height, img.width)
        x, y = next(
            (x, y) for x in range(out.width) for y in range(out.height) if out.getpixel((x, y))
        )
        assert abs(x - 180 * s) <= 1 and y == 20 * s

    def test_screen_mapping(self):
        lay, cam = gw.LANDSCAPE, 50.0
        # Attack runs left to right at PX_PER_FT; the near viewport edge is cam.
        assert lay.screen(cam, 0, cam) == (gw.L_VP_LEFT, gw.L_VP_TOP)
        x_net, y_mid = lay.screen(gw.GOAL_LINE, gw.NET_CENTER_B, cam)
        assert x_net == gw.L_VP_LEFT + (gw.GOAL_LINE - cam) * gw.PX_PER_FT
        assert y_mid == pytest.approx(gw.LH / 2, abs=1)
        # The whole width of the rink fits the frame, and as much ice is in
        # view as in the portrait clip.
        assert lay.screen(cam, gw.RINK_W, cam)[1] < gw.LH
        assert (gw.L_VP_RIGHT - gw.L_VP_LEFT) / gw.PX_PER_FT == gw.VP_FT
        assert lay.in_view(gw.L_VP_LEFT + 1, 0) and not lay.in_view(gw.L_VP_LEFT - 1, 500)

    @pytest.mark.parametrize("layout", [gw.PORTRAIT, gw.LANDSCAPE])
    def test_drawn_rink_lines_up_with_screen(self, layout):
        # The attacking goal line on the cropped rink sits where screen()
        # puts a = GOAL_LINE, with the camera at its far stop.
        cam = gw.RINK_L + gw.VP_HEADROOM_FT - gw.VP_FT
        frame = layout.view(layout.rink(), cam)
        assert frame.size == layout.size
        x, y = layout.screen(gw.GOAL_LINE, 25.0, cam)  # off the crease, between the dots
        red = Image.new("RGB", (1, 1), gw.RED_LINE).getpixel((0, 0))
        near = {
            frame.getpixel((round(x) + dx, round(y) + dy))
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        }
        assert red in near

    def test_landscape_clip_frames_and_card(self):
        frames = replay(straight_rush(start_a=160, frames_to_goal=20, after=15))
        g = gw.analyse(goal(), frames)
        portrait = sum(1 for _ in gw.replay_frames(g))
        clip = list(gw.replay_frames(g, landscape=True))
        assert len(clip) == portrait
        assert {f.size for f in clip} == {(gw.LW, gw.LH)}
        assert clip[-1].size == gw.closing_card_landscape(g).size


# ── One named goal (--game/--event) ─────────────────────────────────────


class FakeClient:
    """httpx.Client stand-in serving one `landing` payload."""

    def __init__(self, landing, status=200):
        self.landing, self.status, self.urls = landing, status, []

    def __call__(self, **_):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **_):
        self.urls.append(url)
        landing, status = self.landing, self.status

        class Res:
            def raise_for_status(self):
                if status != 200:
                    raise gw.httpx.HTTPStatusError("nope", request=None, response=None)

            def json(self):
                return landing

        return Res()


class TestFindGoal:
    def test_by_event_id(self):
        g = gw.find_goal(landing_payload(), 11)
        assert (g["event_id"], g["team"], g["replay_url"]) == (11, "LAK", "https://x/11.json")

    def test_unknown_event_and_shootout_goal_are_none(self):
        assert gw.find_goal(landing_payload(), 999) is None
        assert gw.find_goal(landing_payload(), 12) is None  # shootout


class TestRenderOne:
    def run(self, landing, event_id=11, frames=None, **kwargs):
        encoded = {}

        def encode(clip, size=(gw.VW, gw.VH)):
            encoded["size"], encoded["first"] = size, next(iter(clip))
            return b"MP4"

        client = FakeClient(landing)
        with (
            patch.object(gw.httpx, "Client", client),
            patch.object(gw, "fetch_replay", return_value=frames) as fetch,
            patch.object(gw, "encode_mp4", side_effect=encode),
            patch.object(gw, "get_client", side_effect=AssertionError("no Supabase")),
            patch.object(sp, "ship_video", side_effect=AssertionError("never posts")),
        ):
            code = gw.render_one(1, event_id, **kwargs)
        return code, encoded, fetch, client

    def test_renders_the_named_goal_without_supabase(self, tmp_path):
        frames = replay(straight_rush(start_a=160, frames_to_goal=20, after=15))
        out = tmp_path / "hero" / "slavin.mp4"
        code, encoded, fetch, client = self.run(landing_payload(), frames=frames, out=out)
        assert code == 0 and out.read_bytes() == b"MP4"
        assert client.urls == [f"{gw.NHL_WEB}/gamecenter/1/landing"]
        assert fetch.call_args.args[0] == "https://x/11.json"
        assert encoded["size"] == (gw.VW, gw.VH) and encoded["first"].size == (gw.VW, gw.VH)

    def test_landscape_default_path_in_social_out(self, tmp_path):
        frames = replay(straight_rush(start_a=160, frames_to_goal=20, after=15))
        with patch.object(sp, "OUT_DIR", tmp_path):
            code, encoded, _, _ = self.run(landing_payload(), frames=frames, landscape=True)
        assert code == 0 and (tmp_path / "goal-1-11-landscape.mp4").read_bytes() == b"MP4"
        assert encoded["size"] == (gw.LW, gw.LH) and encoded["first"].size == (gw.LW, gw.LH)

    def test_goal_without_a_tracking_replay_is_a_clear_error(self, tmp_path, capsys):
        landing = landing_payload()
        del landing["summary"]["scoring"][0]["goals"][1]["pptReplayUrl"]
        code, encoded, fetch, _ = self.run(landing, out=tmp_path / "x.mp4")
        assert code == 1 and not encoded and not fetch.called
        assert "has no tracking replay" in capsys.readouterr().err
        assert not (tmp_path / "x.mp4").exists()

    def test_unfetchable_replay_is_a_clear_error(self, tmp_path, capsys):
        code, encoded, _, _ = self.run(landing_payload(), frames=None, out=tmp_path / "x.mp4")
        assert code == 1 and not encoded
        assert "couldn't be fetched" in capsys.readouterr().err

    def test_unknown_event_lists_the_games_goals(self, capsys):
        code, encoded, _, _ = self.run(landing_payload(), event_id=999)
        assert code == 1 and not encoded
        err = capsys.readouterr().err
        assert "no goal with event id 999" in err and "10 (B), 11 (B)" in err

    def test_game_fetch_failure(self, capsys):
        with patch.object(gw.httpx, "Client", FakeClient({}, status=404)):
            assert gw.render_one(1, 11) == 1
        assert "game 1 fetch failed" in capsys.readouterr().err


class TestCli:
    def test_weekly_arguments_unchanged(self):
        args = gw.parse_args(["goal-of-week", "--date", "2026-01-16", "--dry-run"])
        assert (args.date, args.dry_run, args.game, args.landscape) == (
            date(2026, 1, 16),
            True,
            None,
            False,
        )
        assert gw.parse_args(["goal-of-week"]).dry_run is False

    def test_named_goal_implies_dry_run(self):
        args = gw.parse_args(
            [
                "goal-of-week",
                "--game",
                "2025021237",
                "--event",
                "1113",
                "--landscape",
                "--out",
                "a.mp4",
            ]
        )
        assert (args.game, args.event, args.landscape, args.out, args.dry_run) == (
            2025021237,
            1113,
            True,
            Path("a.mp4"),
            True,
        )

    @pytest.mark.parametrize(
        "argv",
        [
            ["--game", "1"],  # --event missing
            ["--event", "2"],  # --game missing
            ["--landscape"],  # landscape is for a named goal only
            ["--out", "a.mp4"],
            ["--game", "1", "--event", "2", "--date", "2026-01-16"],
            [],  # the kind is still required
        ],
    )
    def test_bad_combinations_exit(self, argv):
        full = argv if argv == [] else ["goal-of-week", *argv]
        with pytest.raises(SystemExit) as e:
            gw.parse_args(full)
        assert e.value.code == 2

    def test_main_dispatches_weekly_run(self):
        with (
            patch.object(gw, "run", return_value=0) as run,
            patch.object(gw, "render_one") as one,
        ):
            assert gw.main(["goal-of-week", "--date", "2026-01-16"]) == 0
        run.assert_called_once_with(day=date(2026, 1, 16), dry_run=False)
        one.assert_not_called()

    def test_main_dispatches_named_goal(self):
        with (
            patch.object(gw, "run") as run,
            patch.object(gw, "render_one", return_value=0) as one,
        ):
            assert gw.main(["goal-of-week", "--game", "1", "--event", "2", "--landscape"]) == 0
        one.assert_called_once_with(1, 2, landscape=True, out=None)
        run.assert_not_called()


class TestWeeklyPathUnchanged:
    def test_week_renders_portrait_and_ships(self):
        frames = replay(straight_rush(start_a=160, frames_to_goal=20, after=15))
        picked = gw.analyse(goal(), frames)
        encoded = {}

        def encode(clip, *args, **kwargs):
            encoded["args"], encoded["kwargs"] = args, kwargs
            encoded["first"] = next(iter(clip))
            return b"MP4"

        with (
            patch.object(gw, "week_games", return_value=[1]),
            patch.object(gw.httpx, "Client", FakeClient(landing_payload())),
            patch.object(gw, "pick_goal", return_value=(picked, None)),
            patch.object(gw, "encode_mp4", side_effect=encode),
            patch.object(sp, "ship_video", return_value=0) as ship,
        ):
            assert gw.post_goal_of_week(None, date(2026, 1, 16), dry_run=True) == 0
        assert encoded["args"] == () and encoded["kwargs"] == {}  # default 1080 x 1920
        assert encoded["first"].size == (gw.VW, gw.VH)
        assert ship.call_args.args[1:4] == (gw.KIND, "goal-of-week-2026-01-16", b"MP4")

    def test_encode_mp4_default_size_is_portrait(self):
        class Proc:
            stdin = type("S", (), {"write": lambda *a: None, "close": lambda *a: None})()
            stderr = type("E", (), {"read": lambda *a: b""})()

            def wait(self):
                return 1  # stop before reading an output file

        with (
            patch.object(gw.shutil, "which", return_value="ffmpeg"),
            patch.object(gw.subprocess, "Popen", return_value=Proc()) as popen,
            pytest.raises(RuntimeError),
        ):
            gw.encode_mp4([])
        cmd = popen.call_args.args[0]
        assert cmd[cmd.index("-s") + 1] == "1080x1920"


# ── Video publishing (social_posts) ─────────────────────────────────────


class TestShipVideo:
    def test_dry_run_writes_the_clip_and_captions(self, tmp_path):
        with patch.object(sp, "OUT_DIR", tmp_path):
            assert sp.ship_video(None, "goal-of-week", "gotw-x", b"MP4", "cap", True) == 0
        assert (tmp_path / "gotw-x.mp4").read_bytes() == b"MP4"
        assert {p.name for p in tmp_path.glob("*.txt")} == {
            "gotw-x-instagram.txt",
            "gotw-x-facebook.txt",
        }

    def test_reel_container_is_a_feed_shared_reel_at_the_goal_frame(self):
        calls = []

        def fake_post(path, token, **params):
            calls.append((path, params))
            return {"id": "c1"}

        with (
            patch.object(sp, "graph_post", side_effect=fake_post),
            patch.object(sp, "wait_for_container") as wait,
        ):
            assert sp.publish_instagram_reel("ig", "tok", ["https://v.mp4"], "cap", 9000) == "c1"
        (path, params), (pub_path, _) = calls
        assert path == "ig/media" and pub_path == "ig/media_publish"
        assert params["media_type"] == "REELS" and params["video_url"] == "https://v.mp4"
        assert params["share_to_feed"] == "true" and params["thumb_offset"] == "9000"
        assert wait.call_args.kwargs == {
            "tries": sp.VIDEO_POLL_TRIES,
            "seconds": sp.VIDEO_POLL_SECONDS,
        }

    def test_facebook_video_goes_to_the_video_host_by_url(self):
        class Res:
            status_code = 200

            def json(self):
                return {"id": "v9"}

        with patch.object(sp.httpx, "post", return_value=Res()) as post:
            assert sp.publish_facebook_video("page", "tok", ["https://v.mp4"], "cap", 9000) == "v9"
        url = post.call_args.args[0]
        data = post.call_args.kwargs["data"]
        assert url == f"{sp.GRAPH_VIDEO}/page/videos"
        assert data["file_url"] == "https://v.mp4" and data["description"] == "cap"

    def test_publish_everywhere_skips_done_platforms_and_passes_extras(self, monkeypatch):
        monkeypatch.setenv("META_PAGE_TOKEN", "tok")
        monkeypatch.setenv("IG_USER_ID", "ig")
        monkeypatch.setenv("FB_PAGE_ID", "fb")
        seen = {}

        def publisher(name):
            def fn(account, token, urls, text, **extra):
                seen[name] = extra
                return f"{name}-id"

            return fn

        platforms = {
            "instagram": ("IG_USER_ID", publisher("ig")),
            "facebook": ("FB_PAGE_ID", publisher("fb")),
        }
        with patch.object(sp, "record") as record:
            code = sp.publish_everywhere(
                None, "k", "key", ["u"], "cap", platforms, {"instagram"}, thumb_offset_ms=5
            )
        assert code == 0 and seen == {"fb": {"thumb_offset_ms": 5}}
        assert record.call_args.args[4] == "published"
