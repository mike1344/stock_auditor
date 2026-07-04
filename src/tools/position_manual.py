# src/tools/position_manual.py
import csv

def calc_position_manual(filepath: str):
    groups = {}

    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row['code']
            action = row['action']
            volume = int(row['volume'])
            price = float(row['price'])

            if code not in groups:
                groups[code] = {
                    'name': row.get('name', code),
                    'buy_vol': 0,
                    'buy_amount': 0,
                    'sell_vol': 0,
                }

            if action == '买入':
                groups[code]['buy_vol'] += volume
                groups[code]['buy_amount'] += price * volume
            elif action == '卖出':
                groups[code]['sell_vol'] += volume

    results = []
    for code, data in groups.items():
        shares = data['buy_vol'] - data['sell_vol']
        if shares > 0:
            avg_cost = data['buy_amount'] / data['buy_vol'] if data['buy_vol'] > 0 else 0
            results.append({
                'code': code,
                'name': data['name'],
                'shares': shares,
                'avg_cost': round(avg_cost, 2),
            })

    if not results:
        print("当前无持仓")
        return

    print(f"持仓股票数: {len(results)}")
    for r in results:
        print(f"{r['code']} {r['name']} | 持仓: {r['shares']}股 | 均价: {r['avg_cost']}元")

if __name__ == "__main__":
    calc_position_manual('data/processed/full_trades.csv')