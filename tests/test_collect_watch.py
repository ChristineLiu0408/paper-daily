import datetime as dt
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts.collect_watch import (
    SourceFailure,
    classify_relevance,
    collect_watch,
    dedupe_records,
    discover_dblp_index_toc_keys,
    empty_candidate_archive,
    empty_conference_state,
    fetch_dblp_toc,
    find_keyword_matches,
    empty_journal_seen_state,
    journal_record_date_policy,
    layered_ai_social_relevance,
    load_config,
    parse_feed_entries,
    retrieval_query_terms,
    render_markdown,
    topic_label_for_title,
    update_report_index,
    update_candidate_archive,
    write_reports,
)


CONFIG = load_config(Path("config/watches.yml"))


class CollectWatchTest(unittest.TestCase):
    def test_candidate_archive_preserves_first_capture_and_deduplicates(self) -> None:
        archive = empty_candidate_archive()
        payload = {
            "watch_id": "monthly-top-psychology-journal-watch",
            "part_name": "Monthly Top Psychology Journal Watch",
            "candidates": [
                {
                    "relevance_label": "high",
                    "topic_label": "emotional_social",
                    "title": "AI companion and loneliness",
                    "source": "Example Journal",
                    "link": "https://example.com/paper",
                    "matched_ai_terms": ["AI companion"],
                    "matched_social_terms": ["loneliness"],
                }
            ],
        }

        update_candidate_archive(archive, payload, "2026-07-01T00:00:00+00:00")
        update_candidate_archive(archive, payload, "2026-07-15T00:00:00+00:00")

        self.assertEqual(len(archive["papers"]), 1)
        paper = archive["papers"][0]
        self.assertEqual(paper["first_captured_at"], "2026-07-01T00:00:00+00:00")
        self.assertEqual(paper["last_captured_at"], "2026-07-15T00:00:00+00:00")
        self.assertEqual(paper["watch_ids"], ["monthly-top-psychology-journal-watch"])
    def test_part1_is_monthly_and_uses_rss_novelty_for_missing_dates(self) -> None:
        watch = CONFIG["watches"][0]

        self.assertEqual(watch["id"], "monthly-applied-ai-journal-watch")
        self.assertEqual(watch["frequency"], "monthly")
        self.assertEqual(watch["date_format"], "%Y-%m")
        self.assertEqual(watch["journal_recency_policy"], "entry_date_or_available_online_or_feed_novelty")

    def test_ai_and_llm_use_word_boundaries(self) -> None:
        keywords = ["AI", "LLM", "robot", "agent"]

        self.assertEqual(find_keyword_matches("This is an AI companion study.", keywords), ["AI"])
        self.assertEqual(find_keyword_matches("An LLM self-disclosure study.", keywords), ["LLM"])
        self.assertEqual(find_keyword_matches("A social robot study.", keywords), ["robot"])
        self.assertEqual(find_keyword_matches("An AI agent study.", keywords), ["AI", "agent"])
        self.assertEqual(find_keyword_matches("The claim is about algorithmic work.", keywords), [])
        self.assertEqual(find_keyword_matches("A tallman example should not match.", keywords), [])
        self.assertEqual(find_keyword_matches("Agency should not match.", keywords), [])

    def test_layered_part1_keeps_title_ai_and_marker_as_high(self) -> None:
        watch = CONFIG["watches"][0]
        record = {
            "title": "AI companion use and loneliness",
            "summary_slice": "",
            "source": "Computers in Human Behavior",
            "source_type": "journal",
        }

        classified = classify_relevance(record, watch, CONFIG)

        self.assertIsNotNone(classified)
        self.assertEqual(classified["relevance_label"], "high")
        self.assertIn("AI", classified["matched_ai_terms"])
        self.assertIn("companion", classified["matched_social_terms"])

    def test_layered_part1_title_ai_only_is_ai(self) -> None:
        watch = CONFIG["watches"][0]
        record = {
            "title": "A field study of ChatGPT at work",
            "summary_slice": "Participants described social support.",
            "source": "Computers in Human Behavior",
            "source_type": "journal",
        }

        classified = classify_relevance(record, watch, CONFIG)

        self.assertIsNotNone(classified)
        self.assertEqual(classified["relevance_label"], "AI")
        self.assertEqual(classified["matched_social_terms"], [])

    def test_layered_part1_reject_is_not_kept(self) -> None:
        watch = CONFIG["watches"][0]

        classified = classify_relevance(
            {
                "title": "AI improves scheduling efficiency",
                "summary_slice": "",
                "source": "Computers in Human Behavior",
                "source_type": "journal",
            },
            watch,
            CONFIG,
        )
        self.assertIsNotNone(classified)
        self.assertEqual(classified["relevance_label"], "AI")
        self.assertIsNone(
            classify_relevance(
                {
                    "title": "Time-resolved Coulomb explosion imaging",
                    "summary_slice": "",
                    "source": "Nature Communications",
                    "source_type": "journal",
                },
                watch,
                CONFIG,
            )
        )

    def test_layered_part1_summary_or_author_bio_does_not_change_title_label(self) -> None:
        watch = CONFIG["watches"][0]
        record = {
            "title": "Consumer choice in retail environments",
            "summary_slice": "This abstract mentions AI and emotional support.",
            "authors": ["Researcher bio: studies AI and social support"],
            "source": "Journal of Marketing",
            "source_type": "journal",
        }

        result = layered_ai_social_relevance(record, watch, CONFIG)

        self.assertEqual(result["relevance_label"], "reject")
        self.assertEqual(result["matched_ai_terms"], [])
        self.assertEqual(result["matched_social_terms"], [])

    def test_layered_part1_examples_are_kept(self) -> None:
        watch = CONFIG["watches"][0]
        titles = [
            "Intimacy with Socially Interactive AI",
            "Effects of Empathetic Responses on Human–Chatbot Interactions",
            "Exploring the Effects of an AI Voice Assistant on Drivers' Loneliness",
        ]

        for title in titles:
            with self.subTest(title=title):
                classified = classify_relevance(
                    {
                        "title": title,
                        "summary_slice": "",
                        "source": "Computers in Human Behavior",
                        "source_type": "journal",
                    },
                    watch,
                    CONFIG,
                )
                self.assertIsNotNone(classified)
                self.assertEqual(classified["relevance_label"], "high")
                self.assertIn("emotional_social", classified["topic_label"])

    def test_trust_no_longer_triggers_high(self) -> None:
        watch = CONFIG["watches"][0]
        result = layered_ai_social_relevance(
            {
                "title": "AI trust in automated systems",
                "summary_slice": "",
                "source": "Management Science",
                "source_type": "journal",
            },
            watch,
            CONFIG,
        )

        self.assertEqual(result["relevance_label"], "AI")
        self.assertEqual(result["matched_social_terms"], [])

    def test_human_ai_dash_variants_are_recognized(self) -> None:
        keywords = CONFIG["ai_object_terms"]

        self.assertTrue(find_keyword_matches("Human-AI collaboration", keywords))
        self.assertTrue(find_keyword_matches("Human–AI collaboration", keywords))
        self.assertTrue(find_keyword_matches("Human—AI collaboration", keywords))

    def test_driver_requires_transport_context_for_transport_topic(self) -> None:
        self.assertNotEqual(topic_label_for_title("Drivers of AI adoption in firms"), "transport_automation")
        self.assertEqual(topic_label_for_title("AI takeover alerts for drivers in automated vehicles"), "transport_automation")

    def test_requested_topic_examples(self) -> None:
        watch = CONFIG["watches"][0]
        examples = [
            (
                "Effects of Empathetic Responses, Agent Identity, and Gender Match on Users' Self-Disclosure of Depression and Perceptions in Human–Chatbot Interactions",
                "high",
                "emotional_social",
            ),
            ("Human-AI Collaborative Learning Ecosystem for Programming Learning", "AI", "education_learning"),
            ("The Dual Effects of Coworker AI Usage on Employee Job Crafting", "AI", "organization_work"),
            ("Exploring the Effects of an AI Voice Assistant on Drivers' Loneliness", "high", "emotional_social"),
        ]
        for title, label, topic in examples:
            with self.subTest(title=title):
                result = layered_ai_social_relevance(
                    {"title": title, "summary_slice": "", "source": "Example", "source_type": "journal"},
                    watch,
                    CONFIG,
                )
                self.assertEqual(result["relevance_label"], label)
                self.assertEqual(result["topic_label"], topic)

    def test_strict_relevance_labels_context_match_medium(self) -> None:
        watch = {
            "id": "strict-fixture",
            "part_name": "Strict Fixture",
            "frequency": "weekly",
            "relevance_mode": "strict",
            "keywords_ref": "strict_keywords",
        }
        record = {
            "title": "A qualitative study of digital services",
            "summary_slice": "Participants discussed chatbot companionship during stressful periods.",
            "source": "CHI",
            "source_type": "conference",
        }

        classified = classify_relevance(record, watch, CONFIG)

        self.assertIsNotNone(classified)
        self.assertEqual(classified["relevance_label"], "medium")
        self.assertEqual(classified["matched_keywords"], ["chatbot companionship"])

    def test_part2_uses_part1_title_only_terms(self) -> None:
        watch = CONFIG["watches"][1]
        high = {
            "title": "AI and emotion in social connection",
            "summary_slice": "This paper studies loneliness and belonging.",
            "source": "Psychological Science",
            "source_type": "journal",
        }
        ai_only = {
            "title": "GenAI and judgment",
            "summary_slice": "No social marker is available.",
            "source": "Psychological Review",
            "source_type": "journal",
        }
        reject = {
            "title": "Judgment and decision making",
            "summary_slice": "A study of conversational agent use.",
            "source": "American Psychologist",
            "source_type": "journal",
        }

        self.assertEqual(classify_relevance(high, watch, CONFIG)["relevance_label"], "high")
        self.assertIn("emotion", classify_relevance(high, watch, CONFIG)["matched_social_terms"])
        self.assertEqual(classify_relevance(ai_only, watch, CONFIG)["relevance_label"], "AI")
        self.assertIsNone(classify_relevance(reject, watch, CONFIG))

    def test_part3_uses_part1_title_only_terms(self) -> None:
        watch = CONFIG["watches"][2]
        result = classify_relevance(
            {
                "title": "LLM emotional support in human-AI interaction",
                "summary_slice": "",
                "source": "arXiv cs.HC",
                "source_type": "preprint",
            },
            watch,
            CONFIG,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["relevance_label"], "high")

    def test_part3_uses_confirmed_main_conference_toc(self) -> None:
        watch = CONFIG["watches"][2]
        dblp_source = next(source for source in watch["sources"] if source["api"] == "dblp")
        openalex_source = next(source for source in watch["sources"] if source["api"] == "openalex")
        confirmed = {source["name"]: source for source in watch["sources"] if source.get("dblp_toc_patterns")}

        openalex_terms = retrieval_query_terms(watch, CONFIG, openalex_source)

        self.assertEqual(dblp_source["name"], "CHI")
        self.assertEqual(dblp_source["publication_scope"], "main_conference")
        self.assertEqual(dblp_source["dblp_toc_patterns"], ["db/conf/chi/chi{year}.bht"])
        self.assertEqual(dblp_source["years"], [2026])
        self.assertEqual(confirmed["HRI"]["dblp_toc_patterns"], ["db/conf/hri/hri{year}.bht"])
        self.assertEqual(confirmed["EMNLP"]["dblp_toc_patterns"], ["db/conf/emnlp/emnlp{year}.bht"])
        self.assertEqual(set(confirmed), {"CHI", "HRI", "EMNLP"})
        self.assertEqual(openalex_terms, ["Human-AI", "human-AI", "chatbot", "AI companion", "generative AI", "LLM", "large language model"])
        self.assertNotIn("AI", openalex_terms)
        self.assertNotIn("robot", openalex_terms)

    def test_acl_and_naacl_discover_main_conference_volumes_from_the_dblp_index(self) -> None:
        watch = CONFIG["watches"][2]
        acl = next(source for source in watch["sources"] if source["name"] == "ACL")
        naacl = next(source for source in watch["sources"] if source["name"] == "NAACL")

        self.assertEqual(acl["api"], "dblp_index")
        self.assertEqual(acl["years"], [2026])
        self.assertEqual(naacl["api"], "dblp_index")
        self.assertEqual(naacl["years"], [2027])
        self.assertEqual(acl["include_toc_title_terms"], ["Long Papers", "Short Papers"])

    def test_dblp_index_discovery_keeps_only_main_long_and_short_volumes(self) -> None:
        source = {
            "dblp_index_url": "https://dblp.org/db/conf/acl/index.html",
            "dblp_index_href_regex": "/db/conf/acl/acl{year}-\\d+\\.html$",
            "include_toc_title_terms": ["Long Papers", "Short Papers"],
            "exclude_toc_title_terms": ["Findings", "Workshop"],
            "years": [2026],
        }
        index = b'''<a href="https://dblp.org/db/conf/acl/acl2026-1.html">one</a>
        <a href="https://dblp.org/db/conf/acl/acl2026-2.html">two</a>
        <a href="https://dblp.org/db/conf/acl/acl2026-3.html">three</a>'''
        pages = {
            "https://dblp.org/db/conf/acl/index.html": index,
            "https://dblp.org/db/conf/acl/acl2026-1.html": b"<title>ACL 2026 - Long Papers</title>",
            "https://dblp.org/db/conf/acl/acl2026-2.html": b"<title>ACL 2026 - Short Papers</title>",
            "https://dblp.org/db/conf/acl/acl2026-3.html": b"<title>ACL 2026 - Findings</title>",
        }

        with mock.patch("scripts.collect_watch.request_bytes", side_effect=lambda url: pages[url]):
            toc_keys = discover_dblp_index_toc_keys(source)

        self.assertEqual(toc_keys, ["db/conf/acl/acl2026-1.bht", "db/conf/acl/acl2026-2.bht"])

    def test_dblp_toc_fetch_uses_exact_venue_year_inventory(self) -> None:
        source = {
            "name": "CHI",
            "source_type": "conference",
            "group": "hci",
            "dblp_toc_patterns": ["db/conf/chi/chi{year}.bht"],
            "years": [2026],
        }
        response = {
            "result": {
                "hits": {
                    "hit": [
                        {
                            "info": {
                                "title": "AI companionship in social settings",
                                "authors": {"author": [{"text": "Ada Example"}]},
                                "year": "2026",
                                "url": "https://dblp.org/rec/conf/chi/Example26",
                                "doi": "10.1145/example",
                                "venue": "CHI",
                            }
                        }
                    ]
                }
            }
        }

        with mock.patch("scripts.collect_watch.request_json", return_value=response) as request_json:
            records = fetch_dblp_toc(source)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "CHI")
        self.assertEqual(records[0]["year"], "2026")
        self.assertIn("toc%3Adb%2Fconf%2Fchi%2Fchi2026.bht%3A", request_json.call_args.args[0])

    def test_dblp_toc_fetch_paginates_to_collect_the_full_inventory(self) -> None:
        source = {"name": "CHI", "source_type": "conference", "dblp_toc_patterns": ["db/conf/chi/chi{year}.bht"], "years": [2026]}
        first_page = {
            "result": {
                "hits": {
                    "@total": "2",
                    "hit": [{"info": {"title": "AI companion", "year": "2026", "url": "https://dblp.org/1"}}],
                }
            }
        }
        second_page = {
            "result": {
                "hits": {
                    "@total": "2",
                    "hit": [{"info": {"title": "AI loneliness", "year": "2026", "url": "https://dblp.org/2"}}],
                }
            }
        }

        with mock.patch("scripts.collect_watch.request_json", side_effect=[first_page, second_page]) as request_json:
            records = fetch_dblp_toc(source)

        self.assertEqual([record["title"] for record in records], ["AI companion", "AI loneliness"])
        self.assertIn("f=0", request_json.call_args_list[0].args[0])
        self.assertIn("f=1", request_json.call_args_list[1].args[0])

    def test_conference_state_stops_after_second_check_without_new_records(self) -> None:
        watch = CONFIG["watches"][2]
        conference_source = next(source for source in watch["sources"] if source["name"] == "CHI")
        state = empty_conference_state()
        record = {
            "title": "AI companionship in social settings",
            "source": "CHI",
            "source_type": "conference",
            "date": "2026",
            "year": "2026",
            "link": "https://dblp.org/rec/conf/chi/Example26",
            "doi": "10.1145/example",
        }
        timestamp = dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc)

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([record], [])):
            first = collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)
        first_state = first["conference_state"]["conferences"]["CHI:2026"]
        self.assertEqual(first_state["check_count"], 1)
        self.assertEqual(first_state["status"], "active")

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([record], [])):
            second = collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)
        second_state = second["conference_state"]["conferences"]["CHI:2026"]
        self.assertEqual(second_state["check_count"], 2)
        self.assertEqual(second_state["new_record_count"], 0)
        self.assertEqual(second_state["status"], "complete")

        with mock.patch("scripts.collect_watch.fetch_source") as fetch_source:
            third = collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)
        fetch_source.assert_not_called()
        chi_result = next(item for item in third["source_results"] if item["source"] == conference_source["name"])
        self.assertTrue(chi_result["skipped"])

    def test_conference_state_continues_until_two_successful_inventories_match(self) -> None:
        watch = CONFIG["watches"][2]
        state = empty_conference_state()
        base = {
            "source": "CHI",
            "source_type": "conference",
            "date": "2026",
            "year": "2026",
        }
        first = {**base, "title": "AI companionship", "link": "https://dblp.org/1", "doi": "10.1145/1"}
        added = {**base, "title": "AI loneliness", "link": "https://dblp.org/2", "doi": "10.1145/2"}
        timestamp = dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc)

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([first], [])):
            collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)
        with mock.patch("scripts.collect_watch.fetch_source", return_value=([first, added], [])):
            second = collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)
        second_state = second["conference_state"]["conferences"]["CHI:2026"]
        self.assertEqual(second_state["new_record_count"], 1)
        self.assertEqual(second_state["status"], "active")

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([first, added], [])):
            third = collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)
        self.assertEqual(third["conference_state"]["conferences"]["CHI:2026"]["status"], "complete")

    def test_empty_conference_inventory_does_not_start_the_two_check_rule(self) -> None:
        watch = CONFIG["watches"][2]
        state = empty_conference_state()
        timestamp = dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc)

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([], [])):
            collect_watch(watch, CONFIG, 31, timestamp, source_types={"conference"}, conference_state=state)

        self.assertEqual(state["conferences"], {})

    def test_part3_does_not_include_semantic_scholar_source(self) -> None:
        watch = CONFIG["watches"][2]

        self.assertNotIn("semantic_scholar", [source.get("api") for source in watch["sources"]])

    def test_cscw_is_reserved_for_a_verified_rolling_pacmhci_source(self) -> None:
        watch = CONFIG["watches"][2]
        cscw = next(source for source in watch["sources"] if source["name"] == "CSCW")

        self.assertEqual(cscw["source_type"], "rolling_journal")
        self.assertEqual(cscw["collection_mode"], "rolling_pacmhci")
        self.assertFalse(cscw["enabled"])

    def test_part3_arxiv_only_uses_hc_and_cy_recent_records(self) -> None:
        watch = CONFIG["watches"][2]
        arxiv_sources = [source for source in watch["sources"] if source["api"] == "arxiv"]

        self.assertEqual([source["arxiv_category"] for source in arxiv_sources], ["cs.HC", "cs.CY"])
        self.assertEqual([source["max_results"] for source in arxiv_sources], [250, 250])

    def test_part3_raw_records_keep_ai_only_and_reject_for_counting(self) -> None:
        watch = {
            "id": "weekly-conference-preprint-watch",
            "part_name": "Weekly Conference & Preprint Watch",
            "frequency": "weekly",
            "source_policy": "conference_preprint_api",
            "relevance_mode": "layered_ai_social",
            "ai_object_terms_ref": "ai_object_terms",
            "relationship_markers_ref": "relationship_social_markers",
            "sources": [{"name": "arXiv cs.HC", "source_type": "preprint", "api": "arxiv"}],
        }
        fetched = [
            {"title": "AI emotional support for loneliness", "source": "arXiv cs.HC", "link": "https://example.com/1", "date": "2026-07-06"},
            {"title": "AI benchmark for planning", "source": "arXiv cs.HC", "link": "https://example.com/2", "date": "2026-07-06"},
            {"title": "Social connection in online communities", "source": "arXiv cs.HC", "link": "https://example.com/3", "date": "2026-07-06"},
        ]

        with mock.patch("scripts.collect_watch.fetch_source", return_value=(fetched, [])):
            payload = collect_watch(watch, CONFIG, 7, dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc))

        self.assertEqual(payload["raw_entry_count"], 3)
        self.assertEqual(payload["candidate_count"], 2)
        labels_by_title = {record["title"]: record["relevance_label"] for record in payload["raw_records"]}
        self.assertEqual(labels_by_title["AI emotional support for loneliness"], "high")
        self.assertEqual(labels_by_title["AI benchmark for planning"], "AI")
        self.assertEqual(labels_by_title["Social connection in online communities"], "reject")

    def test_dedupe_prefers_doi_then_url_then_title_year(self) -> None:
        records = [
            {"title": "A", "year": "2026", "doi": "10.1/a", "link": "https://one.example/a"},
            {"title": "Different", "year": "2026", "doi": "10.1/a", "link": "https://two.example/a"},
            {"title": "B", "year": "2026", "doi": "", "link": "https://example.com/paper?utm=1"},
            {"title": "B again", "year": "2026", "doi": "", "link": "https://example.com/paper"},
            {"title": "Same Title!", "year": "2026", "doi": "", "link": ""},
            {"title": "Same title", "year": "2026", "doi": "", "link": ""},
        ]

        deduped = dedupe_records(records)

        self.assertEqual(len(deduped), 3)
        self.assertEqual(deduped[0]["title"], "A")
        self.assertEqual(deduped[-1]["title"], "Same Title!")

    def test_parse_rss_fixture(self) -> None:
        xml = b"""
        <rss version="2.0">
          <channel>
            <item>
              <title>AI companion use in everyday life</title>
              <author>Ada Example</author>
              <link>https://example.com/paper</link>
              <pubDate>Mon, 06 Jul 2026 00:00:00 GMT</pubDate>
              <description>Participants described AI companionship.</description>
            </item>
          </channel>
        </rss>
        """

        entries = parse_feed_entries(
            xml,
            {"name": "Computers in Human Behavior", "source_type": "journal"},
        )

        self.assertEqual(entries[0]["title"], "AI companion use in everyday life")
        self.assertEqual(entries[0]["authors"], ["Ada Example"])
        self.assertEqual(entries[0]["source"], "Computers in Human Behavior")
        self.assertEqual(entries[0]["summary_slice"], "Participants described AI companionship.")

    def test_report_generation_writes_markdown_and_json(self) -> None:
        watch = CONFIG["watches"][0]
        payload = {
            "watch_id": watch["id"],
            "part_name": watch["part_name"],
            "frequency": watch["frequency"],
            "generated_at": "2026-07-06T00:00:00+00:00",
            "since_days": 7,
            "candidate_count": 1,
            "raw_records": [
                {
                    "title": "AI companion use in everyday life",
                    "authors": ["Ada Example"],
                    "source": "Computers in Human Behaviour",
                    "date": "2026-07-06",
                    "year": "2026",
                    "link": "https://example.com/paper",
                    "summary_slice": "Participants described AI companionship.",
                    "matched_ai_terms": ["AI"],
                    "matched_social_terms": ["companion"],
                    "topic_label": "emotional_social",
                    "relevance_label": "high",
                }
            ],
            "candidates": [
                {
                    "title": "AI companion use in everyday life",
                    "authors": ["Ada Example"],
                    "source": "Computers in Human Behavior",
                    "date": "2026-07-06",
                    "year": "2026",
                    "link": "https://example.com/paper",
                    "summary_slice": "Participants described AI companionship.",
                    "matched_keywords": ["AI companion"],
                    "matched_ai_terms": ["AI"],
                    "matched_social_terms": ["companion"],
                    "topic_label": "emotional_social",
                    "relevance_label": "high",
                }
            ],
            "failed_sources": [SourceFailure("Journal X", "journal", "RSS missing").__dict__],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            md_path, json_path = write_reports(
                payload,
                watch,
                Path(tmpdir),
                dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc),
            )

            self.assertEqual(md_path.name, "2026-07.md")
            self.assertEqual(json_path.name, "2026-07.json")
            md_text = md_path.read_text(encoding="utf-8")
            json_text = json_path.read_text(encoding="utf-8")
            self.assertIn("## High relevance", md_text)
            self.assertIn("matched_social_terms: companion", md_text)
            self.assertNotIn("Ada Example", md_text)
            self.assertNotIn("摘要切片", render_markdown(payload))
            self.assertIn('"candidate_count": 1', json_text)
            self.assertIn('"topic_label": "emotional_social"', json_text)
            self.assertNotIn('"authors"', json_text)
            self.assertNotIn('"summary_slice"', json_text)

    def test_report_generation_writes_all_markdown_and_xlsx_when_raw_records_exist(self) -> None:
        watch = CONFIG["watches"][0]
        payload = {
            "watch_id": watch["id"],
            "part_name": watch["part_name"],
            "frequency": watch["frequency"],
            "generated_at": "2026-07-06T00:00:00+00:00",
            "since_days": 7,
            "source_count": 1,
            "raw_entry_count": 1,
            "candidate_count": 0,
            "source_results": [{"source": "Computers in Human Behavior", "raw_count": 1, "failed": False}],
            "raw_records": [
                {
                    "title": "AI companion use in everyday life",
                    "authors": ["Ada Example"],
                    "source": "Computers in Human Behavior",
                    "date": "2026-07-06",
                    "year": "2026",
                    "link": "https://example.com/paper",
                    "summary_slice": "Participants described AI companionship.",
                    "source_type": "journal",
                    "relevance_label": "high",
                    "topic_label": "emotional_social",
                    "matched_ai_terms": ["AI"],
                    "matched_social_terms": ["companion"],
                }
            ],
            "candidates": [],
            "failed_sources": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(
                payload,
                watch,
                Path(tmpdir),
                dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc),
            )
            all_md_path = Path(tmpdir) / watch["id"] / "2026-07_all.md"
            all_xlsx_path = Path(tmpdir) / watch["id"] / "2026-07_all.xlsx"

            self.assertIn("All Fetched Records", all_md_path.read_text(encoding="utf-8"))
            self.assertNotIn("Ada Example", all_md_path.read_text(encoding="utf-8"))
            with zipfile.ZipFile(all_xlsx_path) as xlsx:
                self.assertIn("xl/worksheets/sheet1.xml", xlsx.namelist())

            update_report_index(Path(tmpdir), {watch["id"]})
            report_index = json.loads((Path(tmpdir) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report_index["reports"]), 1)
            entry = report_index["reports"][0]
            self.assertEqual(entry["watch_id"], watch["id"])
            self.assertEqual(entry["markdown"], f"{watch['id']}/2026-07.md")
            self.assertEqual(entry["json"], f"{watch['id']}/2026-07.json")
            self.assertEqual(entry["xlsx"], f"{watch['id']}/2026-07_all.xlsx")
            self.assertEqual(len(report_index["history"]), 1)
            self.assertEqual(report_index["history"][0]["json"], f"{watch['id']}/2026-07.json")

    def test_journal_date_policy_uses_available_online_but_not_issue_month(self) -> None:
        available_online = {"date": "", "summary_slice": "Publication date: Available online 17 July 2026"}
        issue_month = {"date": "", "summary_slice": "Publication date: December 2026"}

        self.assertEqual(journal_record_date_policy(available_online), ("available_online", "2026-07-17"))
        self.assertEqual(journal_record_date_policy(issue_month), ("feed_novelty", ""))

    def test_journal_seen_state_excludes_first_baseline_then_keeps_new_undated_record(self) -> None:
        watch = CONFIG["watches"][0]
        source = watch["sources"][0]
        state = empty_journal_seen_state()
        timestamp = dt.datetime(2026, 7, 19, tzinfo=dt.timezone.utc)
        baseline = {"title": "AI companion relationships", "source": source["name"], "link": "https://example.com/1", "date": "", "summary_slice": "Publication date: December 2026"}
        added = {"title": "Chatbot emotional support", "source": source["name"], "link": "https://example.com/2", "date": "", "summary_slice": "Publication date: December 2026"}

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([baseline], [])):
            first = collect_watch(watch, CONFIG, 31, timestamp, journal_seen_state=state)
        self.assertEqual(first["raw_entry_count"], 0)
        self.assertTrue(state["sources"][source["name"]]["initialized"])

        with mock.patch("scripts.collect_watch.fetch_source", return_value=([baseline, added], [])):
            second = collect_watch(watch, CONFIG, 31, timestamp, journal_seen_state=state)
        self.assertEqual(second["raw_entry_count"], 1)
        self.assertEqual(second["raw_records"][0]["title"], "Chatbot emotional support")

    def test_collect_watch_records_failed_source_and_fallback(self) -> None:
        watch = {
            "id": "weekly-applied-ai-journal-watch",
            "part_name": "Weekly Applied AI Journal Watch",
            "frequency": "weekly",
            "relevance_mode": "strict",
            "keywords_ref": "strict_keywords",
            "api_fallbacks": ["openalex"],
            "sources": [
                {"name": "Journal X", "source_type": "journal", "rss_url": ""},
            ],
        }
        fallback_record = {
            "title": "AI companion support",
            "authors": ["Ada Example"],
            "source": "Journal X",
            "source_type": "journal",
            "date": "2026-07-06",
            "year": "2026",
            "link": "https://example.com/paper",
            "summary_slice": "",
            "categories": [],
            "venue": "",
        }

        with (
            mock.patch("scripts.collect_watch.fetch_feed", side_effect=ValueError("RSS/Atom URL is not configured")),
            mock.patch("scripts.collect_watch.fetch_openalex", return_value=[fallback_record]),
        ):
            payload = collect_watch(watch, CONFIG, 7, dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc))

        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["failed_sources"][0]["source"], "Journal X")
        self.assertEqual(payload["failed_sources"][-1]["fallback_used"], "openalex")


if __name__ == "__main__":
    unittest.main()
