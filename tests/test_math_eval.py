import unittest

from whisp.math_eval import MathError, evaluate_lines


class TestMathEval(unittest.TestCase):
    def test_evaluate_lines(self):
        out = evaluate_lines([
            "rent = 1,200", "food = 300", "rent + food", "", "2^10", "20% of 50",
            "1/0", "hello", "sum", "10", "ans * 2", "9**9**9", "// 1+1", "avg",
        ])
        self.assertEqual(out, ["1,200", "300", "1,500", None, "1,024", "10", None, None,
                               "1,034", None, "20", None, None, "15"])

    def test_decimals(self):
        self.assertEqual(evaluate_lines(["1/3", "0.1 + 0.2", "$5 * 1.5"]),
                         ["0.3333333333", "0.3", "7.5"])

    def test_redefinition_is_error(self):
        out = evaluate_lines(["x = 1", "x = 2", "x + 1", "y = x", "ans = 5"])
        self.assertIsInstance(out[1], MathError)
        self.assertEqual(out[2], "2")
        self.assertNotIsInstance(out[4], MathError)


if __name__ == "__main__":
    unittest.main()
