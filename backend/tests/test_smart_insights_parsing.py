import unittest

from app.modules.smart_insights import (
    _build_applies_to_label,
    _build_missing_field_rates,
    _build_priority_action_candidates,
    _extract_record,
    _is_missing_scalar,
    _is_missing_topics,
)


class SmartInsightsParsingTests(unittest.TestCase):
    def test_extract_record_from_detail_payload_object_values(self) -> None:
        conversation = {
            "conversation_id": "conv_1",
            "start_time_unix_secs": 1730000000,
            "analysis": {
                "data_collection_results": {
                    "hotel_location": {"value": "general"},
                    "user_intent": {"value": "reservation_new"},
                    "topics": {"value": "other"},
                    "booking_stage": {"value": "pre_booking"},
                    "resolution_status": {"value": "unresolved"},
                    "friction_point": {"value": "agent_understanding_issue"},
                    "knowledge_gap": {"value": "none"},
                    "recommended_internal_action": {"value": "no_action_needed"},
                },
                "evaluation_criteria_results": {
                    "human_escalation": {"result": "success"},
                    "intent_identification": {"result": "success"},
                    "call_cancellation": {"result": "failure"},
                },
            },
        }

        record = _extract_record(conversation, 0)
        self.assertEqual(record["hotel_location"], "general")
        self.assertEqual(record["user_intent"], "reservation_new")
        self.assertEqual(record["topics"], ["other"])
        self.assertEqual(record["booking_stage"], "pre_booking")
        self.assertEqual(record["resolution_status"], "unresolved")
        self.assertEqual(record["primary_friction_point"], "agent_understanding_issue")
        self.assertEqual(record["knowledge_gap_topic"], "none")
        self.assertEqual(record["recommended_internal_action"], "no_action_needed")
        self.assertEqual(record["criteria"]["human_escalation"], "pass")
        self.assertEqual(record["criteria"]["intent_identification"], "pass")
        self.assertEqual(record["criteria"]["call_cancellation"], "fail")

    def test_none_other_no_action_needed_are_not_missing(self) -> None:
        self.assertFalse(_is_missing_scalar("none"))
        self.assertFalse(_is_missing_scalar("other"))
        self.assertFalse(_is_missing_scalar("no_action_needed"))
        self.assertFalse(_is_missing_topics(["none"]))
        self.assertFalse(_is_missing_topics(["other"]))
        self.assertTrue(_is_missing_scalar("unknown"))
        self.assertTrue(_is_missing_topics(["unknown"]))

    def test_missing_rates_treat_explicit_taxonomy_values_as_present(self) -> None:
        records = [
            {
                "hotel_location": "general",
                "recommended_internal_action": "no_action_needed",
                "knowledge_gap_topic": "none",
                "primary_friction_point": "none",
                "user_intent": "other",
                "resolution_status": "resolved",
                "booking_stage": "pre_booking",
                "topics": ["other"],
                "criteria": {
                    "human_escalation": "pass",
                    "intent_identification": "pass",
                    "call_cancellation": "pass",
                },
            }
        ]

        missing_rates, data_coverage_percent = _build_missing_field_rates(records)
        missing_by_field = {item["field"]: item["missingPercent"] for item in missing_rates}
        self.assertEqual(data_coverage_percent, 100.0)
        self.assertEqual(missing_by_field["hotel_location"], 0.0)
        self.assertEqual(missing_by_field["recommended_internal_action"], 0.0)
        self.assertEqual(missing_by_field["knowledge_gap_topic"], 0.0)
        self.assertEqual(missing_by_field["primary_friction_point"], 0.0)
        self.assertEqual(missing_by_field["user_intent"], 0.0)
        self.assertEqual(missing_by_field["resolution_status"], 0.0)
        self.assertEqual(missing_by_field["booking_stage"], 0.0)
        self.assertEqual(missing_by_field["topics"], 0.0)
        self.assertEqual(missing_by_field["human_escalation"], 0.0)
        self.assertEqual(missing_by_field["intent_identification"], 0.0)
        self.assertEqual(missing_by_field["call_cancellation"], 0.0)

    def test_applies_to_label_is_plain_language(self) -> None:
        records = [
            {
                "user_intent": "reservation_new",
                "hotel_location": "berlin",
                "booking_stage": "pre_booking",
                "topics": ["booking"],
            }
        ]
        label = _build_applies_to_label(records)
        self.assertNotIn(":", label)
        self.assertNotIn("_", label)
        self.assertIn("Calls about", label)

    def test_priority_actions_use_human_readable_scope(self) -> None:
        records = [
            {
                "recommended_internal_action": "update_knowledge_base",
                "resolution_status": "unresolved",
                "user_intent": "reservation_new",
                "hotel_location": "general",
                "booking_stage": "pre_booking",
                "topics": ["other"],
                "criteria": {
                    "human_escalation": "fail",
                    "intent_identification": "fail",
                    "call_cancellation": "pass",
                },
            },
            {
                "recommended_internal_action": "update_knowledge_base",
                "resolution_status": "resolved",
                "user_intent": "reservation_new",
                "hotel_location": "general",
                "booking_stage": "pre_booking",
                "topics": ["other"],
                "criteria": {
                    "human_escalation": "pass",
                    "intent_identification": "fail",
                    "call_cancellation": "pass",
                },
            },
        ]
        actions = _build_priority_action_candidates(records, total_calls=len(records))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["actionTitle"], "Update Knowledge Base")
        self.assertNotIn(":", actions[0]["appliesTo"])
        self.assertIn("Calls about", actions[0]["appliesTo"])


if __name__ == "__main__":
    unittest.main()
