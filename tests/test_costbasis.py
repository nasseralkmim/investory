from investory import costbasis
import os
import pytest


@pytest.fixture
def csv_file_1(tmp_path):
    csv_data = '''date,type,ticker,vol,price,name,CNPJ
01/01/2021,buy,AAAA,100,10.,X,Y
01/02/2021,buy,AAAA,100,12.,X,Y'''

    file_path = os.path.join(tmp_path, 'input_file1.csv')
    with open(file_path, 'w') as f:
        f.write(csv_data)
    return file_path


@pytest.fixture
def csv_file_2(tmp_path):
    csv_data = '''date,type,ticker,vol,price,name,CNPJ
01/03/2022,sell,AAAA,50,13.,X,Y
01/05/2022,buy,AAAA,50,9.,X,Y'''

    file_path = os.path.join(tmp_path, 'input_file2.csv')
    with open(file_path, 'w') as f:
        f.write(csv_data)
    return file_path


@pytest.fixture
def csv_file_3(tmp_path):
    csv_data = '''date,type,ticker,vol,price,name,CNPJ
01/03/2023,split,AAAA,200,0.,X,Y
01/05/2023,buy,AAAA,50,4.,X,Y'''

    file_path = os.path.join(tmp_path, 'input_file3.csv')
    with open(file_path, 'w') as f:
        f.write(csv_data)
    return file_path


def test_inventory_cost(csv_file_1, csv_file_2):
    files = [
        csv_file_1,
        csv_file_2,
    ]

    transactions = costbasis.collect_transactions(files)
    transactions = costbasis.adjust_volume(transactions)
    inventory_list = costbasis.generate_aggregate_inventory(transactions)
    df = inventory_list[0].transactions
    assert df.loc[df["date"] == "2021-01-01", "average cost"].values[0] == 10
    assert df.loc[df["date"] == "2021-01-01", "inventory cost"].values[0] == 1000
    assert df.loc[df["date"] == "2021-01-02", "average cost"].values[0] == 11
    assert df.loc[df["date"] == "2021-01-02", "inventory cost"].values[0] == 1000 + 1200
    assert df.loc[df["date"] == "2022-01-03", "average cost"].values[0] == 11
    assert (
        df.loc[df["date"] == "2022-01-03", "inventory cost"].values[0] == 2200 - 11 * 50
    )
    assert df.loc[df["date"] == "2022-01-05", "average cost"].values[0] == 10.5
    assert (
        df.loc[df["date"] == "2022-01-05", "inventory cost"].values[0] == 1650 + 450
    )


def test_inventory_quantities(csv_file_1, csv_file_2):
    files = [
        csv_file_1,
        csv_file_2,
    ]

    transactions = costbasis.collect_transactions(files)
    transactions = costbasis.adjust_volume(transactions)
    inventory_list = costbasis.generate_aggregate_inventory(transactions)
    df = inventory_list[0].transactions
    assert df.loc[df["date"] == "2021-01-01", "inventory"].values[0] == 100
    assert df.loc[df["date"] == "2021-01-02", "inventory"].values[0] == 200
    assert df.loc[df["date"] == "2022-01-03", "inventory"].values[0] == 150
    assert df.loc[df["date"] == "2022-01-05", "inventory"].values[0] == 200


def test_transaction_cost(csv_file_1, csv_file_2):
    files = [
        csv_file_1,
        csv_file_2,
    ]

    transactions = costbasis.collect_transactions(files)
    transactions = costbasis.adjust_volume(transactions)
    inventory_list = costbasis.generate_aggregate_inventory(transactions)
    df = inventory_list[0].transactions
    assert df.loc[df["date"] == "2021-01-01", "transaction cost"].values[0] == 1000
    assert df.loc[df["date"] == "2021-01-02", "transaction cost"].values[0] == 1200
    assert df.loc[df["date"] == "2022-01-03", "transaction cost"].values[0] == -650
    assert df.loc[df["date"] == "2022-01-05", "transaction cost"].values[0] == 450


def test_split_transaction(csv_file_1, csv_file_2, csv_file_3):
    files = [
        csv_file_1,
        csv_file_2,
        csv_file_3,
    ]

    transactions = costbasis.collect_transactions(files)
    transactions = costbasis.adjust_volume(transactions)
    inventory_list = costbasis.generate_aggregate_inventory(transactions)
    df = inventory_list[0].transactions
    print(df)
    assert df.loc[df["date"] == "2023-01-03", "inventory cost"].values[0] == 2100
    assert df.loc[df["date"] == "2023-01-03", "average cost"].values[0] == 2100 / 400
    assert df.loc[df["date"] == "2023-01-05", "inventory cost"].values[0] == 2300
    assert df.loc[df["date"] == "2023-01-05", "average cost"].values[0] == 2300 / 450


if __name__ == "__main__":
    test_split_transaction("test1.csv", "test2.csv", "test3.csv")
