import unittest

from lefly_agent.telemetry import LatencyRecorder


class SequenceClock:
    def __init__(self, *values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class LatencyRecorderTests(unittest.TestCase):
    def test_emits_monotonic_structured_stages_without_raw_text(self):
        records = []
        recorder = LatencyRecorder(
            clock=SequenceClock(10.0, 10.010, 10.025, 10.040),
            sink=records.append,
        )
        trace = recorder.begin(
            "req-1", response_id="resp-1", queue_depth=2, text_length=17
        )

        trace.stage("request_accepted", outcome="accepted")
        trace.stage("route_decided", decision_path="fast_intent")
        trace.stage("response_completed", outcome="completed")

        self.assertEqual(
            [record["stage"] for record in records],
            ["request_received", "request_accepted", "route_decided", "response_completed"],
        )
        self.assertEqual(records[-1]["total_elapsed_ms"], 40.0)
        self.assertTrue(all(record["stage_elapsed_ms"] >= 0 for record in records))
        self.assertTrue(all(record["event"] == "agent.latency.stage" for record in records))
        self.assertTrue(all(record["request_id"] == "req-1" for record in records))
        self.assertTrue(all("text" not in record for record in records))
        self.assertEqual(records[0]["text_length"], 17)

    def test_first_chunk_and_first_delta_are_recorded_once(self):
        records = []
        recorder = LatencyRecorder(
            clock=SequenceClock(1.0, 1.1, 1.2), sink=records.append
        )
        trace = recorder.begin("req", response_id="resp", queue_depth=0)

        trace.stage_once("llm_first_chunk")
        trace.stage_once("llm_first_chunk")

        self.assertEqual(
            [record["stage"] for record in records],
            ["request_received", "llm_first_chunk"],
        )


if __name__ == "__main__":
    unittest.main()
