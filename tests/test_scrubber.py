from jobscout.scrubber import scrub_payload


def test_scrub_payload_masks_the_candidates_name():
    result = scrub_payload({"text": "Jane Doe applied for this role."}, name="Jane Doe")
    assert result == {"text": "[CANDIDATE] applied for this role."}


def test_scrub_payload_masks_name_case_insensitively():
    result = scrub_payload("jane doe is available immediately", name="Jane Doe")
    assert result == "[CANDIDATE] is available immediately"


def test_scrub_payload_masks_current_employer():
    result = scrub_payload("Currently at Foo GmbH as a lead.", employer="Foo GmbH")
    assert result == "Currently at [EMPLOYER] as a lead."


def test_scrub_payload_strips_an_email():
    assert scrub_payload("Reach me at jane@example.com") == "Reach me at [CONTACT]"


def test_scrub_payload_strips_a_phone_number():
    assert scrub_payload("Call +49 151 234 5678 anytime") == "Call [CONTACT] anytime"


def test_scrub_payload_buckets_exact_comp():
    assert scrub_payload("Looking for around €85k") == "Looking for around €80–90k"


def test_scrub_payload_buckets_comp_at_a_bucket_boundary():
    assert scrub_payload("Salary floor is $120k") == "Salary floor is $120–130k"


def test_scrub_payload_preserves_dict_and_list_structure():
    payload = {
        "messages": [{"role": "user", "content": "Contact jane@example.com"}],
        "score": 72,
        "matched": None,
    }
    result = scrub_payload(payload)
    assert result == {
        "messages": [{"role": "user", "content": "Contact [CONTACT]"}],
        "score": 72,
        "matched": None,
    }


def test_scrub_payload_preserves_reasoning_text_with_nothing_to_redact():
    text = "Strong stack fit: JD names React/TypeScript, resume shows 5y React."
    assert scrub_payload(text) == text


def test_scrub_payload_without_name_or_employer_leaves_other_names_alone():
    assert scrub_payload("Jane Doe applied") == "Jane Doe applied"
