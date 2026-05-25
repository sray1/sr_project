"""
Test script for NumPy and Pandas libraries.
This script verifies that both libraries are installed and working correctly.
"""

import numpy as np
import pandas as pd


def test_numpy():
    """Test basic NumPy functionality."""
    print("=" * 50)
    print("Testing NumPy")
    print("=" * 50)
    
    # Test array creation
    print("\n1. Array Creation:")
    arr = np.array([1, 2, 3, 4, 5])
    print(f"   Created array: {arr}")
    print(f"   Array type: {type(arr)}")
    print(f"   Array dtype: {arr.dtype}")
    
    # Test array operations
    print("\n2. Array Operations:")
    arr2 = np.array([10, 20, 30, 40, 50])
    print(f"   Array 1: {arr}")
    print(f"   Array 2: {arr2}")
    print(f"   Addition: {arr + arr2}")
    print(f"   Multiplication: {arr * arr2}")
    
    # Test statistical functions
    print("\n3. Statistical Functions:")
    print(f"   Mean: {np.mean(arr)}")
    print(f"   Std Dev: {np.std(arr)}")
    print(f"   Sum: {np.sum(arr)}")
    print(f"   Min: {np.min(arr)}")
    print(f"   Max: {np.max(arr)}")
    
    # Test multi-dimensional arrays
    print("\n4. Multi-dimensional Arrays:")
    matrix = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    print(f"   3x3 Matrix:\n{matrix}")
    print(f"   Shape: {matrix.shape}")
    print(f"   Transpose:\n{matrix.T}")
    
    # Test array manipulation
    print("\n5. Array Manipulation:")
    print(f"   Reshaped to 1x9: {matrix.reshape(1, 9)}")
    print(f"   Flattened: {matrix.flatten()}")
    
    print("\n✓ NumPy tests passed!")


def test_pandas():
    """Test basic Pandas functionality."""
    print("\n" + "=" * 50)
    print("Testing Pandas")
    print("=" * 50)
    
    # Test DataFrame creation
    print("\n1. DataFrame Creation:")
    data = {
        'Name': ['Alice', 'Bob', 'Charlie', 'Diana'],
        'Age': [25, 30, 35, 28],
        'City': ['New York', 'London', 'Paris', 'Tokyo']
    }
    df = pd.DataFrame(data)
    print(f"   Created DataFrame:\n{df}")
    
    # Test DataFrame info
    print("\n2. DataFrame Information:")
    print(f"   Shape: {df.shape}")
    print(f"   Columns: {list(df.columns)}")
    print(f"   Index: {list(df.index)}")
    print(f"   Dtypes:\n{df.dtypes}")
    
    # Test data selection
    print("\n3. Data Selection:")
    print(f"   First row:\n{df.iloc[0]}")
    print(f"   'Name' column:\n{df['Name']}")
    print(f"   Age > 28:\n{df[df['Age'] > 28]}")
    
    # Test data manipulation
    print("\n4. Data Manipulation:")
    df['Salary'] = [50000, 60000, 70000, 55000]
    print(f"   Added 'Salary' column:\n{df}")
    
    df['Age_plus_5'] = df['Age'] + 5
    print(f"   Added 'Age_plus_5' column:\n{df}")
    
    # Test aggregation
    print("\n5. Aggregation:")
    print(f"   Mean Age: {df['Age'].mean()}")
    print(f"   Sum Salary: {df['Salary'].sum()}")
    print(f"   Group by City:\n{df.groupby('City')['Age'].mean()}")
    
    # Test Series operations
    print("\n6. Series Operations:")
    series = pd.Series([10, 20, 30, 40, 50], index=['a', 'b', 'c', 'd', 'e'])
    print(f"   Created Series:\n{series}")
    print(f"   Series mean: {series.mean()}")
    print(f"   Series describe:\n{series.describe()}")
    
    print("\n✓ Pandas tests passed!")


def test_integration():
    """Test NumPy and Pandas integration."""
    print("\n" + "=" * 50)
    print("Testing NumPy + Pandas Integration")
    print("=" * 50)
    
    # Create DataFrame from NumPy array
    print("\n1. DataFrame from NumPy Array:")
    np_array = np.random.randn(5, 3)
    df_from_np = pd.DataFrame(np_array, columns=['A', 'B', 'C'])
    print(f"   NumPy array:\n{np_array}")
    print(f"   DataFrame:\n{df_from_np}")
    
    # Use NumPy functions on Pandas data
    print("\n2. NumPy Functions on Pandas Data:")
    print(f"   Apply np.sqrt to column 'A':\n{np.sqrt(df_from_np['A'])}")
    print(f"   Apply np.exp to column 'B':\n{np.exp(df_from_np['B'])}")
    
    print("\n✓ Integration tests passed!")


if __name__ == "__main__":
    print("NumPy and Pandas Test Script")
    print(f"NumPy version: {np.__version__}")
    print(f"Pandas version: {pd.__version__}")
    
    try:
        test_numpy()
        test_pandas()
        test_integration()
        print("\n" + "=" * 50)
        print("All tests completed successfully! ✓")
        print("=" * 50)
    except Exception as e:
        print(f"\n✗ Error occurred: {e}")
        import traceback
        traceback.print_exc()