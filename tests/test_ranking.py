"""The sponsorship ranking rule (AC5). No AI and no database: pure rules, so it runs instantly."""
from app.ranking import rank


def make_job(job_id, sponsorship, required, quote="", **extra):
    return {
        "id": job_id, "title": f"Graduate Engineer {job_id}", "company": "Acme", "location": "London",
        "required_skills": required, "optional_skills": [], "sponsorship": sponsorship,
        "sponsorship_quote": quote, "work_mode": "hybrid", "seniority": "graduate",
        "industry": "Technology", "salary_min": None, "salary_max": None, **extra,
    }


def make_profile(**prefs):
    return {
        "skills": ["Python", "SQL", "Git", "Docker"], "level": "graduate",
        "dream_skills": {"Python": 2, "AWS": 1}, "dream_titles": ["Backend Engineer"],
        "preferences": {"needs_sponsorship": True, **prefs},
    }


def test_sponsoring_job_outranks_no_sponsorship_job_for_visitor_who_needs_sponsorship():
    # The "no sponsorship" job is a PERFECT skill match; the sponsoring job only matches half.
    no_sponsor = make_job("A", "no_sponsorship", ["Python", "SQL", "Git", "Docker"],
                          quote="We are unable to offer visa sponsorship")
    sponsor = make_job("B", "sponsors", ["Python", "Kubernetes", "Go", "AWS"],
                       quote="Visa sponsorship is available")

    ranked = rank(make_profile(), [no_sponsor, sponsor])

    assert [r.job["id"] for r in ranked] == ["B", "A"]
    assert ranked[1].blocked is True
    assert ranked[1].block_quote == "We are unable to offer visa sponsorship"
    assert ranked[0].blocked is False
    assert ranked[1].score > ranked[0].score  # proves the order comes from the rule, not the score


def test_unclear_sponsorship_is_not_blocked():
    ranked = rank(make_profile(), [make_job("A", "unclear", ["Python"])])
    assert ranked[0].blocked is False


def test_no_sponsorship_job_is_fine_when_visitor_does_not_need_it():
    profile = make_profile(needs_sponsorship=False)
    ranked = rank(profile, [make_job("A", "no_sponsorship", ["Python", "SQL"], quote="no sponsorship")])
    assert ranked[0].blocked is False


def test_salary_more_than_20_percent_below_minimum_is_blocked():
    profile = make_profile(needs_sponsorship=False, min_salary=40000)
    low = make_job("A", "unclear", ["Python"], salary_min=25000, salary_max=28000)
    ok = make_job("B", "unclear", ["Python"], salary_min=30000, salary_max=34000)
    ranked = rank(profile, [low, ok])
    assert [r.job["id"] for r in ranked] == ["B", "A"]
    assert ranked[1].blocked and "salary" in ranked[1].block_reason.lower()


def test_each_result_explains_matches_and_missing_skills():
    job = make_job("A", "sponsors", ["Python", "AWS"])
    result = rank(make_profile(), [job])[0]
    assert result.strong_matches == ["Python"]
    assert result.missing_skills == ["AWS"]
    assert "Python" in result.why and "AWS" in result.why
