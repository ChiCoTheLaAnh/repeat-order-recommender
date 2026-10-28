import importlib
from pathlib import Path
import unittest


class LoadReportTests(unittest.TestCase):
    def test_fallback_share_follows_shared_request_sequence(self):
        module = importlib.import_module('scripts.load_test')
        self.assertTrue(hasattr(module,'request_mode'),'Traffic schedule must use configured unknown fraction')
        modes = [module.request_mode(number,.1) for number in range(100)]
        self.assertEqual(modes.count('popularity_fallback'),10)
        for start in range(0,100,10):
            self.assertEqual(modes[start:start+10].count('popularity_fallback'),1)

    def test_actual_errors_and_throughput_denominators(self):
        self.assertTrue(Path('scripts/load_test.py').exists(),'Local load measurements required')
        module = importlib.import_module('scripts.load_test')
        result = module.summarize([10.,20.,30.,40.],[200,200,503,0],2.)
        self.assertEqual(result['completed_requests'],4)
        self.assertEqual(result['error_rate'],.5)
        self.assertEqual(result['throughput_requests_per_second'],2.)
        self.assertEqual(result['successful_requests_per_second'],1.)
        self.assertEqual(result['latency_all_attempts']['p50_ms'],25.)
        self.assertEqual(result['latency_successful']['p50_ms'],15.)


if __name__ == '__main__':
    unittest.main()
