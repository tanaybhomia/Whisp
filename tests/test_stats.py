import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import unittest
from whisp.stats import StatsTracker

class TestStatsTracker(unittest.TestCase):
    def test_increment_and_save(self):
        st = StatsTracker()
        initial_created = st.stats.get("notes_created", 0)
        st.increment("notes_created", 1)
        self.assertEqual(st.stats["notes_created"], initial_created + 1)
        st.save()

if __name__ == "__main__":
    unittest.main()
