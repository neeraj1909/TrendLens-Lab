import unittest
from unittest.mock import patch

from socialrum.drafts import generate_draft


class GenerateDraftTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "Neeraj",
            "role": "backend engineer",
            "topics": ["Python", "AI agents"],
            "voice": "curious and practical",
        }
        self.trend = {
            "topic": "local-first AI",
            "summary": "More teams are experimenting with local models.",
            "source": "User-provided research notes",
        }

    def test_template_draft_uses_only_provided_context_and_is_labeled(self):
        result = generate_draft(self.profile, self.trend)

        self.assertEqual(result["mode"], "template")
        self.assertEqual(result["model"], None)
        self.assertIn("local-first AI", result["draft"])
        self.assertIn("backend engineer", result["draft"])
        self.assertNotIn(self.trend["summary"], result["draft"])
        self.assertEqual(result["source"], self.trend["source"])
        self.assertIn("unverified imported post", result["source_label"].lower())
        self.assertIn("unverified", result["draft"].lower())
        self.assertTrue(result["draft"].strip())
        self.assertNotIn("published", result)

    def test_ollama_prompt_delimits_untrusted_source_and_labels_generated_output(self):
        with patch("socialrum.drafts._ollama_draft", return_value="A generated thought.") as generate:
            result = generate_draft(self.profile, self.trend, model="local-model")

        prompt = generate.call_args.args[1]
        self.assertIn("untrusted", prompt.lower())
        self.assertIn("<untrusted_imported_post>", prompt)
        self.assertIn("</untrusted_imported_post>", prompt)
        self.assertIn(self.trend["summary"], prompt)
        self.assertIn("unverified", prompt.lower())
        self.assertIn("A generated thought.", result["draft"])
        self.assertIn("unverified", result["draft"].lower())
        self.assertEqual(result["source"], self.trend["source"])
        self.assertIn("unverified imported post", result["source_label"].lower())

    def test_invalid_or_empty_context_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_draft({}, self.trend)
        with self.assertRaises(ValueError):
            generate_draft(self.profile, {"topic": "   "})

    def test_requested_local_model_reports_missing_ollama_transparently(self):
        # An unreachable local Ollama server is a real, actionable failure;
        # the code must not silently swap to a paid service or template.
        with self.assertRaises(RuntimeError) as caught:
            generate_draft(self.profile, self.trend, model="socialrum-test-model")
        message = str(caught.exception).lower()
        self.assertIn("ollama", message)
        self.assertIn("socialrum-test-model", message)


if __name__ == "__main__":
    unittest.main()
