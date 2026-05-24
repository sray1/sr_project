def fibonacci(n):
    """Return the nth Fibonacci number."""
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

def fibonacci_sequence(count):
    """Return a list of the first 'count' Fibonacci numbers."""
    return [fibonacci(i) for i in range(1, count + 1)]

if __name__ == "__main__":
    print("Fibonacci numbers (first 10):")
    print(fibonacci_sequence(10))