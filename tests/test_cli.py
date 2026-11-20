from linak_leg import cli


def test_cli_commands(make_leg):
    leg, sim = make_leg()
    lines = []

    def out(*a, **k):
        lines.append(" ".join(str(x) for x in a))

    cli.run(leg, iter(["s", "g 310", "d 5", "raw", "help", "g abc", "q"]), out)
    text = "\n".join(lines)
    assert "position  3000" in text
    assert "Moving to 3100" in text and "Moving to 3050" in text
    assert abs(sim.mm - 305) <= 2
    assert "Commands:" in text and "Type a number" in text
