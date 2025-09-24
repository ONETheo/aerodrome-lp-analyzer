import argparse
import json
import sys
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, getcontext, InvalidOperation
from pathlib import Path
from typing import Dict, List, Tuple, Optional

getcontext().prec = 50

@dataclass
class LPMetrics:
    """Performance metrics for LP position analysis"""
    wallet: str
    blocks: str
    initial_capital: Decimal
    final_capital: Decimal
    net_profit: Decimal
    xirr: Optional[Decimal]
    twr: Decimal
    apr: Decimal
    apy: Decimal
    divergence_loss: Decimal
    vs_hodl: Decimal
    vs_hodl_apr: Decimal
    hodl_apr: Decimal
    rebalance_count: int
    days_active: Decimal
    btc_price_start: Decimal
    btc_price_end: Decimal

class AerodromeAnalyzer:
    """Analyzes Aerodrome LP positions from transaction data"""

    def __init__(self, data_file: Path, wallet_address: str = None):
        self.data_file = data_file
        self.data = self._load_data()
        self.actions = sorted(self.data['actions'], key=lambda a: self._parse_ts(a['timestamp']))
        self.wallet_address = wallet_address
        self._augment_actions_with_prices()
        self.btc_prices = self._extract_btc_prices()

    def _load_data(self) -> Dict:
        """Load transaction data from JSON file"""
        with open(self.data_file, 'r') as f:
            return json.load(f)

    @staticmethod
    def _parse_ts(ts: str) -> datetime:
        # Accepts ISO strings with or without timezone; default to UTC for naive
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            # Fallback trimming if "+00:00" formatting mismatch
            dt = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', ''))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _augment_actions_with_prices(self) -> None:
        """Ensure each action has an implied BTC price derived from cbbtc/usdc/cash_flow."""
        for a in self.actions:
            cbbtc = Decimal(str(a.get('cbbtc', 0)))
            usdc = Decimal(str(a.get('usdc', 0)))
            if cbbtc and cbbtc != 0:
                # If cash_flow exists in USD, |cash_flow| = cbbtc*price + usdc for add/remove legs
                cf = a.get('cash_flow', None)
                if cf is not None:
                    total = abs(Decimal(str(cf)))
                    implied = (total - usdc) / cbbtc if cbbtc != 0 else None
                else:
                    # Fallback: if only tokens present, require explicit price elsewhere; leave None
                    implied = None
            else:
                implied = None
            a['implied_price'] = float(implied) if implied and implied > 0 else None

    def _extract_btc_prices(self) -> Dict[str, Decimal]:
        """Extract implied BTC prices from transaction cash flows"""
        prices: List[Decimal] = []
        for action in self.actions:
            ip = action.get('implied_price')
            if ip:
                try:
                    prices.append(Decimal(str(ip)))
                except InvalidOperation:
                    continue

        if prices:
            return {
                'first': prices[0],
                'last': prices[-1],
                'average': sum(prices) / Decimal(len(prices))
            }
        raise ValueError("Cannot extract BTC prices from transaction data. Ensure 'cbbtc' and 'cash_flow' values are present to infer price.")

    def analyze(self) -> LPMetrics:
        """Perform complete analysis of LP position"""
        dates = self._get_date_range()
        tokens = self._calculate_token_flows()
        cash_flows = self._calculate_cash_flows(tokens)
        rebalances = self._count_rebalances()
        xirr = self._calculate_xirr(cash_flows, tokens, dates)
        twr = self._calculate_twr()
        apr, apy = self._calculate_apr_apy_from_twr(twr, dates['days'])
        divergence_loss = self._calculate_divergence_loss_cashflow_matched()
        vs_hodl = self._calculate_vs_hodl_cashflow_matched()
        hodl_apr, vs_hodl_apr = self._calculate_hodl_metrics(dates['days'], vs_hodl)

        wallet = self._extract_wallet()
        blocks = self._extract_block_range()

        final_capital = cash_flows['realized_withdrawn'] + cash_flows['terminal_valuation']
        net_profit = final_capital - cash_flows['total_deployed']

        return LPMetrics(
            wallet=wallet,
            blocks=blocks,
            initial_capital=cash_flows['first_inflow'],
            final_capital=final_capital,
            net_profit=net_profit,
            xirr=xirr,
            twr=twr,
            apr=apr,
            apy=apy,
            divergence_loss=divergence_loss,
            vs_hodl=vs_hodl,
            vs_hodl_apr=vs_hodl_apr,
            hodl_apr=hodl_apr,
            rebalance_count=rebalances,
            days_active=dates['days'],
            btc_price_start=self.btc_prices['first'],
            btc_price_end=self.btc_prices['last']
        )

    def _get_date_range(self) -> Dict:
        """Calculate date range of activity with fractional days"""
        first_dt = self._parse_ts(self.actions[0]['timestamp'])
        last_dt = self._parse_ts(self.actions[-1]['timestamp'])
        days = Decimal((last_dt - first_dt).total_seconds()) / Decimal(86400)
        return {
            'first': first_dt,
            'last': last_dt,
            'days': days if days > 0 else Decimal('1')
        }

    def _calculate_token_flows(self) -> Dict[str, Decimal]:
        """Calculate gross and net token movements"""
        flows = {
            'cbbtc_in': Decimal('0'),
            'usdc_in': Decimal('0'),
            'cbbtc_out': Decimal('0'),
            'usdc_out': Decimal('0'),
            'cbbtc_fees': Decimal('0'),
            'usdc_fees': Decimal('0')
        }

        for action in self.actions:
            cbbtc = Decimal(str(action.get('cbbtc', 0)))
            usdc = Decimal(str(action.get('usdc', 0)))
            ev = action['event']
            if ev in ['Mint', 'IncreaseLiquidity']:
                flows['cbbtc_in'] += cbbtc
                flows['usdc_in'] += usdc
            elif ev in ['Burn', 'DecreaseLiquidity']:
                flows['cbbtc_out'] += cbbtc
                flows['usdc_out'] += usdc
            elif ev == 'Collect':
                flows['cbbtc_fees'] += cbbtc
                flows['usdc_fees'] += usdc

        flows['cbbtc_net_out'] = flows['cbbtc_out'] - flows['cbbtc_in']  # >0 means net withdrawn
        flows['usdc_net_out'] = flows['usdc_out'] - flows['usdc_in']      # >0 means net withdrawn
        flows['cbbtc_position'] = flows['cbbtc_in'] - flows['cbbtc_out']  # remaining in LP
        flows['usdc_position'] = flows['usdc_in'] - flows['usdc_out']      # remaining in LP
        return flows

    def _calculate_cash_flows(self, tokens: Dict) -> Dict[str, Decimal]:
        """Calculate USD cash flows, realized withdrawals, and terminal valuation"""
        realized_withdrawn = Decimal('0')
        total_deployed = Decimal('0')
        fees_realized = Decimal('0')
        first_inflow = None

        price_last = self.btc_prices['last']

        for a in self.actions:
            ev = a['event']
            cf = Decimal(str(a['cash_flow']))
            if ev in ['Mint', 'IncreaseLiquidity']:
                total_deployed += abs(cf)
                if first_inflow is None:
                    first_inflow = abs(cf)
            elif ev in ['Burn', 'DecreaseLiquidity']:
                realized_withdrawn += cf
            elif ev == 'Collect':
                realized_withdrawn += cf
                fees_realized += cf

        # Terminal valuation for any remaining position (mark-to-market at last price)
        terminal_valuation = tokens['cbbtc_position'] * price_last + tokens['usdc_position']

        return {
            'first_inflow': first_inflow or Decimal('0'),
            'total_deployed': total_deployed,
            'realized_withdrawn': realized_withdrawn,
            'fees_realized': fees_realized,
            'terminal_valuation': terminal_valuation
        }

    def _calculate_xirr(self, cash_flows: Dict, tokens: Dict, dates: Dict) -> Optional[Decimal]:
        """Calculate XIRR (money-weighted return) via bisection with dated flows; adds terminal mark-to-market if open."""
        try:
            flows: List[Tuple[datetime, Decimal]] = []
            for a in self.actions:
                dt = self._parse_ts(a['timestamp'])
                cf = Decimal(str(a['cash_flow']))
                flows.append((dt, cf))

            # Add terminal valuation if net position remains
            if tokens['cbbtc_position'] != 0 or tokens['usdc_position'] != 0:
                terminal_value = tokens['cbbtc_position'] * self.btc_prices['last'] + tokens['usdc_position']
                if terminal_value != 0:
                    flows.append((dates['last'], terminal_value))

            if len(flows) < 2:
                return None

            flows.sort(key=lambda x: x[0])
            t0 = flows[0][0]

            def years_frac(d: datetime) -> float:
                return (d - t0).total_seconds() / 31557600.0  # 365.25-day year

            def npv(rate: float) -> float:
                if rate <= -0.999999999:
                    return float('inf')
                total = 0.0
                for d, cf in flows:
                    t = years_frac(d)
                    total += float(cf) / ((1.0 + rate) ** t)
                return total

            # Bracket root
            low, high = -0.9999, 1000.0
            f_low, f_high = npv(low), npv(high)
            # Expand high if needed
            if f_low * f_high > 0:
                for h in [1, 5, 10, 50, 100, 500, 1000, 5000, 10000]:
                    high = float(h)
                    f_high = npv(high)
                    if f_low * f_high < 0:
                        break
            if f_low * f_high > 0:
                # Try shrink low
                for l in [-0.9, -0.99, -0.999, -0.9999]:
                    low = float(l)
                    f_low = npv(low)
                    if f_low * f_high < 0:
                        break
            if f_low * f_high > 0:
                return None

            # Bisection
            for _ in range(200):
                mid = (low + high) / 2.0
                f_mid = npv(mid)
                if abs(f_mid) < 1e-6 or abs(high - low) < 1e-12:
                    return Decimal(str(mid * 100.0))
                if f_low * f_mid < 0:
                    high = mid
                    f_high = f_mid
                else:
                    low = mid
                    f_low = f_mid

            mid = (low + high) / 2.0
            return Decimal(str(mid * 100.0))
        except Exception:
            return None

    def _count_rebalances(self) -> int:
        """Count rebalancing events (decrease followed by increase within 5 min)"""
        count = 0
        for i in range(len(self.actions) - 1):
            if (self.actions[i]['event'] in ['DecreaseLiquidity', 'Burn'] and
                self.actions[i+1]['event'] in ['IncreaseLiquidity', 'Mint']):
                curr_ts = self._parse_ts(self.actions[i]['timestamp'])
                next_ts = self._parse_ts(self.actions[i+1]['timestamp'])
                if (next_ts - curr_ts).total_seconds() < 300:
                    count += 1
        return count

    def _calculate_twr(self) -> Decimal:
        """
        Calculate Time-Weighted Return by linking rebalancing subperiods:
        subperiod return r = (withdrawn_usd - redeployed_usd) / redeployed_usd for pairs within 5 minutes.
        """
        linked = Decimal('1')
        for i in range(len(self.actions) - 1):
            a0, a1 = self.actions[i], self.actions[i+1]
            if (a0['event'] in ['DecreaseLiquidity', 'Burn'] and
                a1['event'] in ['IncreaseLiquidity', 'Mint']):
                t0 = self._parse_ts(a0['timestamp'])
                t1 = self._parse_ts(a1['timestamp'])
                if (t1 - t0).total_seconds() <= 300:
                    withdrawn = Decimal(str(a0['cash_flow']))          # positive USD
                    redeployed = abs(Decimal(str(a1['cash_flow'])))    # positive USD
                    if redeployed > 0:
                        period_r = (withdrawn - redeployed) / redeployed
                        linked *= (Decimal('1') + period_r)

        # Include pure fee collections as performance (no external capital change)
        # Approximate by treating fees as return on capital; if no redeploy baseline, skip.
        last_redeploy_base: Optional[Decimal] = None
        for a in self.actions:
            if a['event'] in ['IncreaseLiquidity', 'Mint']:
                last_redeploy_base = abs(Decimal(str(a['cash_flow'])))
            elif a['event'] == 'Collect' and last_redeploy_base and last_redeploy_base > 0:
                fee = Decimal(str(a['cash_flow']))  # positive USD
                linked *= (Decimal('1') + (fee / last_redeploy_base))

        return (linked - Decimal('1')) * Decimal('100')

    def _calculate_apr_apy_from_twr(self, twr_pct: Decimal, days: Decimal) -> Tuple[Decimal, Decimal]:
        """Annualize using linked TWR for comparability"""
        if days <= 0:
            return Decimal('0'), Decimal('0')
        twr = float(twr_pct) / 100.0
        apr = (twr * (365.0 / float(days))) * 100.0
        apy = ((1.0 + twr) ** (365.0 / float(days)) - 1.0) * 100.0
        return Decimal(str(apr)), Decimal(str(apy))

    def _calculate_vs_hodl_cashflow_matched(self) -> Decimal:
        """
        Cash-flow matched HODL benchmark:
        - On add: buy the exact cbbtc/usdc tokens (outflow).
        - On remove: sell the exact cbbtc/usdc at that action's implied price (inflow).
        - At end: mark remaining HODL tokens to last price.
        Return LP_net - HODL_net in USD.
        """
        lp_net = Decimal('0')
        hodl_net = Decimal('0')
        hodl_cbbtc = Decimal('0')
        hodl_usdc = Decimal('0')

        last_price = self.btc_prices['last']

        for a in self.actions:
            ev = a['event']
            cf = Decimal(str(a['cash_flow']))
            cbbtc = Decimal(str(a.get('cbbtc', 0)))
            usdc = Decimal(str(a.get('usdc', 0)))
            ip = a.get('implied_price')
            price = Decimal(str(ip)) if ip else last_price

            lp_net += cf

            if ev in ['Mint', 'IncreaseLiquidity']:
                # Buy tokens for HODL (outflow)
                hodl_cbbtc += cbbtc
                hodl_usdc += usdc
                hodl_net -= (cbbtc * price + usdc)
            elif ev in ['Burn', 'DecreaseLiquidity']:
                # Sell tokens for HODL (inflow)
                hodl_cbbtc -= cbbtc
                hodl_usdc -= usdc
                hodl_net += (cbbtc * price + usdc)
            elif ev == 'Collect':
                # No HODL equivalent fees
                pass

        # Terminal valuation of remaining HODL tokens
        hodl_net += (hodl_cbbtc * last_price + hodl_usdc)

        return lp_net - hodl_net

    def _calculate_divergence_loss_cashflow_matched(self) -> Decimal:
        """
        Divergence/impermanent loss approximation:
        IL = (LP excluding fees) - HODL, using cash-flow matched approach.
        """
        lp_ex_fees = Decimal('0')
        hodl_net = Decimal('0')
        hodl_cbbtc = Decimal('0')
        hodl_usdc = Decimal('0')
        last_price = self.btc_prices['last']

        for a in self.actions:
            ev = a['event']
            cf = Decimal(str(a['cash_flow']))
            cbbtc = Decimal(str(a.get('cbbtc', 0)))
            usdc = Decimal(str(a.get('usdc', 0)))
            ip = a.get('implied_price')
            price = Decimal(str(ip)) if ip else last_price

            if ev in ['Mint', 'IncreaseLiquidity', 'Burn', 'DecreaseLiquidity']:
                lp_ex_fees += cf
            # HODL leg
            if ev in ['Mint', 'IncreaseLiquidity']:
                hodl_cbbtc += cbbtc
                hodl_usdc += usdc
                hodl_net -= (cbbtc * price + usdc)
            elif ev in ['Burn', 'DecreaseLiquidity']:
                hodl_cbbtc -= cbbtc
                hodl_usdc -= usdc
                hodl_net += (cbbtc * price + usdc)

        hodl_net += (hodl_cbbtc * last_price + hodl_usdc)
        return lp_ex_fees - hodl_net

    def _calculate_hodl_metrics(self, days: Decimal, vs_hodl_usd: Decimal) -> Tuple[Decimal, Decimal]:
        """Compute HODL APR and LP outperformance APR based on cash-flow matched benchmark"""
        if days <= 0:
            return Decimal('0'), Decimal('0')

        # Estimate HODL initial capital as the absolute first outflow
        first = self.actions[0]
        first_ip = Decimal(str(first.get('implied_price') or self.btc_prices['first']))
        initial_value = Decimal(str(first.get('cbbtc', 0))) * first_ip + Decimal(str(first.get('usdc', 0)))

        # Approximate HODL total return by simulating only the first leg for APR baseline
        # For multi-leg precise APR, one would compute IRR of HODL flows; here keep simple baseline.
        hodl_return_pct = Decimal('0')
        if initial_value > 0:
            # Convert vs_hodl in USD to a percentage vs initial baseline, then annualize
            hodl_return_pct = (vs_hodl_usd / initial_value)

        hodl_apr = hodl_return_pct * (Decimal('365') / days) * Decimal('100')
        # LP APR already computed from TWR; outperformance APR:
        # This method reports "vs_hodl_apr" as annualized differential based on initial baseline.
        vs_hodl_apr = hodl_apr  # keep semantic: this value is the differential vs HODL
        return hodl_apr, vs_hodl_apr

    def _extract_wallet(self) -> str:
        """Extract wallet address from data or use generic"""
        if 'wallet' in self.data:
            return self.data['wallet']
        if self.wallet_address:
            return self.wallet_address
        return "LP Position"

    def _extract_block_range(self) -> str:
        """Extract block range from data or calculate from timestamps"""
        if 'start_block' in self.data and 'end_block' in self.data:
            return f"{self.data['start_block']}-{self.data['end_block']}"
        if 'summary' in self.data:
            summary = self.data['summary']
            if 'start_block' in summary and 'end_block' in summary:
                return f"{summary['start_block']}-{summary['end_block']}"
        dates = self._get_date_range()
        start_date = dates['first'].strftime('%Y-%m-%d')
        end_date = dates['last'].strftime('%Y-%m-%d')
        return f"{start_date} to {end_date}"

def _decode_uniswap_v3_price_from_swap_log_data(data_hex: str, token0_decimals: int, token1_decimals: int) -> Optional[Decimal]:
    """
    Decode sqrtPriceX96 from Uniswap V3 Swap log 'data' and compute token1 per token0 and token0 per token1 prices.
    Returns USDC per BTC if plausible given expected ranges.
    """
    if len(data_hex) < 192*2:
        return None
    try:
        # words: [amount0, amount1, sqrtPriceX96, liquidity, tick]
        sqrt_price_x96 = int(data_hex[128:192], 16)
        if sqrt_price_x96 <= 0:
            return None
        # Base ratios without decimals adjustments
        ratio = (Decimal(sqrt_price_x96) / (Decimal(2) ** 96)) ** 2  # token1/token0 ignoring decimals
        # Adjust for decimals: price token1 in token0 units
        dec_factor_1_per_0 = Decimal(10) ** Decimal(token0_decimals - token1_decimals)
        price_1_per_0 = ratio * dec_factor_1_per_0
        # Inverse: token0 per token1
        price_0_per_1 = (Decimal(1) / ratio) * (Decimal(10) ** Decimal(token1_decimals - token0_decimals))
        # Heuristic: BTC in USDC should be ~1e4 to 1e6
        if Decimal('10000') <= price_1_per_0 <= Decimal('1000000'):
            return price_1_per_0
        if Decimal('10000') <= price_0_per_1 <= Decimal('1000000'):
            return price_0_per_1
        # If neither plausible, return the larger as a fallback
        return max(price_1_per_0, price_0_per_1)
    except Exception:
        return None

def get_pool_price_from_swap_logs(block_number: int, api_key: str) -> Optional[Decimal]:
    """Get cbBTC-USDC pool's actual price from Swap events at specific block"""
    try:
        import requests
    except ImportError:
        print("ERROR: requests library not available for fetching pool prices")
        return None

    BASE_URL = "https://api.basescan.org/api"
    POOL_ADDRESS = "0x4e962BB3889Bf030368F56810A9c96B83CB3E778"  # cbBTC-USDC pool
    SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

    # cbBTC has 8 decimals, USDC has 6; token0/token1 order can vary, handle both via decoder
    TOKEN0_DECIMALS = 8
    TOKEN1_DECIMALS = 6

    params = {
        "module": "logs",
        "action": "getLogs",
        "address": POOL_ADDRESS,
        "fromBlock": max(block_number - 200, 0),  # widen window slightly
        "toBlock": block_number,
        "topic0": SWAP_TOPIC,
        "apikey": api_key
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=15)
        if response.status_code != 200:
            print(f"  API error: HTTP {response.status_code}")
            return None

        data = response.json()
        if data.get("status") != "1":
            msg = data.get('message', 'Unknown error')
            if "rate limit" in msg.lower():
                print("  API rate limit reached")
            else:
                print(f"  API response: {msg}")
            return None

        results = data.get("result") or []
        if not results:
            print(f"  No swap events found near block {block_number}")
            return None

        for log in reversed(results):
            data_hex = log.get("data", "")[2:]
            price = _decode_uniswap_v3_price_from_swap_log_data(data_hex, TOKEN0_DECIMALS, TOKEN1_DECIMALS)
            if price and Decimal('1000') < price < Decimal('10000000'):
                return price

        print(f"  Could not extract valid price from {len(results)} swap events")
        return None

    except requests.exceptions.RequestException as e:
        print(f"  Network error: {str(e)}")
        return None
    except Exception as e:
        print(f"  Unexpected error: {str(e)}")
        return None

def fetch_from_basescan(wallet: str, api_key: str, start_block: Optional[int] = None, end_block: Optional[int] = None) -> Optional[Dict]:
    """
    Fetch cbBTC-USDC LP transactions from Basescan API.

    NOTE: This is where we originally fetched the data for full_example_data.json
    The actual implementation that worked used transaction receipts and event logs.
    """
    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' library required for fetching from Basescan")
        print("Install with: pip install requests")
        print("Or use existing data files: full_example_data.json or sample_data.json")
        return None

    BASE_URL = "https://api.basescan.org/api"
    NFT_MANAGER = "0x827922686190790b37229fd06084350e74485b72"

    print(f"Fetching LP transactions for {wallet}...")
    print(f"Blocks: {start_block or 'earliest'} to {end_block or 'latest'}")

    params = {
        "module": "account",
        "action": "txlist",
        "address": wallet,
        "startblock": start_block if start_block else 0,
        "endblock": end_block if end_block else 99999999,
        "sort": "asc",
        "apikey": api_key
    }

    response = requests.get(BASE_URL, params=params, timeout=20)
    data = response.json()

    if data.get("status") != "1":
        print(f"Error: {data.get('message', 'Failed to fetch transactions')}")
        return None

    all_txs = data.get("result") or []
    lp_txs = [tx for tx in all_txs if tx.get("to", "").lower() == NFT_MANAGER.lower()]

    print(f"Found {len(lp_txs)} LP transactions")

    if not lp_txs:
        print("No LP transactions found for this wallet")
        return None

    actions: List[Dict] = []

    for tx in lp_txs:
        tx_hash = tx["hash"]
        method_id = tx.get("input", "")[:10] if len(tx.get("input", "")) >= 10 else ""

        if method_id == "0x88316456":
            event = "Mint"
        elif method_id == "0x219f5d17":
            event = "IncreaseLiquidity"
        elif method_id == "0x0c49ccbe":
            event = "DecreaseLiquidity"
        elif method_id == "0xfc6f7865":
            event = "Collect"
        elif method_id == "0x42966c68":
            event = "Burn"
        else:
            continue

        print(f"Processing {event}: {tx_hash[:10]}...")

        time.sleep(0.2)

        receipt_params = {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
            "apikey": api_key
        }

        receipt_response = requests.get(BASE_URL, params=receipt_params, timeout=20)
        receipt_data = receipt_response.json()

        if "result" not in receipt_data or not receipt_data["result"]:
            continue

        receipt = receipt_data["result"]

        ts = datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc).isoformat()

        cbbtc = Decimal('0')
        usdc = Decimal('0')
        token_id = None

        # Event signatures for Uniswap V3 PositionManager-like events
        MINT_SIG = "0x7a53080ba414158be7ec69b987b5fb7d07dee101bff85ac3f90d5c68ca679f40"
        BURN_SIG = "0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496"
        INCREASE_SIG = "0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f"
        DECREASE_SIG = "0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4"
        COLLECT_SIG = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"

        for log in receipt.get("logs", []):
            if not log.get("topics"):
                continue
            topic0 = log["topics"][0]
            if topic0 in [MINT_SIG, BURN_SIG, INCREASE_SIG, DECREASE_SIG, COLLECT_SIG]:
                if len(log["topics"]) > 1:
                    try:
                        token_id = int(log["topics"][1], 16)
                    except Exception:
                        token_id = None
                data_hex = log["data"][2:] if log["data"].startswith("0x") else log["data"]
                try:
                    # For all events, the amounts are laid out in the same words for PositionManager logs
                    amount0_raw = int(data_hex[64:128], 16)
                    amount1_raw = int(data_hex[128:192], 16)
                    # USDC 6 decimals, cbBTC 8 decimals; PositionManager amounts match token order in the position
                    usdc = Decimal(amount0_raw) / Decimal(10**6)
                    cbbtc = Decimal(amount1_raw) / Decimal(10**8)
                    break
                except (ValueError, IndexError, InvalidOperation):
                    continue

        if cbbtc > 0 or usdc > 0:
            block_num = int(tx.get('blockNumber', '0'))
            btc_price = get_pool_price_from_swap_logs(block_num, api_key)

            if not btc_price:
                print(f"\nERROR: Could not fetch pool price for block {block_num}")
                print(f"Transaction: {tx_hash}")
                print("Unable to calculate accurate cash flows without pool price data.")
                print("\nPossible reasons:")
                print("  1. No swap events near this block")
                print("  2. API rate limit or connectivity issue")
                print("  3. Pool was not yet deployed at this block")
                return None

            total_usd = cbbtc * btc_price + usdc

            if event in ["Mint", "IncreaseLiquidity"]:
                cash_flow = -total_usd
            elif event in ["Burn", "DecreaseLiquidity", "Collect"]:
                cash_flow = total_usd
            else:
                continue
        else:
            continue

        action = {
            "timestamp": ts,
            "event": event,
            "token_id": token_id,
            "cbbtc": float(cbbtc),
            "usdc": float(usdc),
            "cash_flow": float(round(cash_flow, 2)),
            "tx": tx_hash
        }
        actions.append(action)
        print(f"  ✓ cbBTC: {cbbtc:.8f}, USDC: {usdc:.2f}, Cash flow: ${cash_flow:.2f}")

    print(f"\nSuccessfully decoded {len(actions)} transactions")

    if not actions:
        print("\nNo valid LP transactions found or decoding failed.")
        print("Please check the wallet address and block range.")
        return None

    actual_start = min(int(tx["blockNumber"]) for tx in lp_txs)
    actual_end = max(int(tx["blockNumber"]) for tx in lp_txs)

    result = {
        "wallet": wallet,
        "start_block": actual_start,
        "end_block": actual_end,
        "actions": actions
    }

    return result

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Analyze Aerodrome LP performance with optional Basescan fetching',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --data-file full_example_data.json
  %(prog)s --data-file sample_data.json

  export BASESCAN_API_KEY=your_key_here
  %(prog)s --fetch 0xYourWallet
  %(prog)s --fetch 0xYourWallet --start-block 35102089 --end-block 35867540

  %(prog)s --format json --data-file full_example_data.json
  %(prog)s --format text --data-file full_example_data.json

Note: XIRR is money-weighted and robust with dated flows; TWR links subperiod returns for strategy comparability.
        '''
    )

    parser.add_argument(
        '--fetch',
        metavar='WALLET',
        help='Fetch data from Basescan for this wallet (requires API key)'
    )
    parser.add_argument(
        '--api-key',
        help='Basescan API key (or set BASESCAN_API_KEY env var)'
    )
    parser.add_argument(
        '--start-block',
        type=int,
        help='Start block for fetching (optional)'
    )
    parser.add_argument(
        '--end-block',
        type=int,
        help='End block for fetching (optional)'
    )

    parser.add_argument(
        '--data-file',
        help='Transaction data JSON file (default: full_example_data.json if exists)'
    )
    parser.add_argument(
        '--format',
        choices=['text', 'json', 'summary'],
        default='summary',
        help='Output format (default: summary)'
    )
    parser.add_argument(
        '--wallet',
        help='Override wallet address for display'
    )

    args = parser.parse_args()

    if args.fetch:
        api_key = args.api_key or os.environ.get('BASESCAN_API_KEY')
        if not api_key:
            print("ERROR: Basescan API key required for fetching")
            print("Either pass --api-key or set BASESCAN_API_KEY environment variable")
            print("Get free API key at: https://basescan.org/apis")
            return 1

        fetched_data = fetch_from_basescan(
            args.fetch,
            api_key,
            args.start_block,
            args.end_block
        )

        if fetched_data:
            output_file = f"lp_data_{args.fetch[:8]}.json"
            with open(output_file, 'w') as f:
                json.dump(fetched_data, f, indent=2)
            print(f"Data saved to: {output_file}")
            data_path = Path(output_file)
        else:
            print("\nFetch implementation incomplete. Use provided example files:")
            print("  python aerodrome_lp_analyzer.py --data-file full_example_data.json")
            return 1
    else:
        if not args.data_file:
            if Path('full_example_data.json').exists():
                args.data_file = 'full_example_data.json'
            else:
                args.data_file = 'xirr_from_receipts.json'

        data_path = Path(args.data_file)
        if not data_path.exists():
            print(f"Error: Data file '{args.data_file}' not found", file=sys.stderr)
            print(f"\nExpected JSON structure:", file=sys.stderr)
            print('''{
  "actions": [
    {
      "timestamp": "2025-09-04T...",
      "event": "IncreaseLiquidity" or "DecreaseLiquidity",
      "cbbtc": 0.00207616,
      "usdc": 1641.79,
      "cash_flow": -1840.15,
      "tx": "0x..."
    },
    ...
  ]
}''', file=sys.stderr)
            return 1

    try:
        analyzer = AerodromeAnalyzer(data_path, wallet_address=args.wallet)
        metrics = analyzer.analyze()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("\nTip: Ensure your data has non-zero 'cbbtc' and corresponding 'cash_flow' values to calculate BTC prices.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error analyzing data: {e}", file=sys.stderr)
        return 1

    if args.format == 'json':
        output = {
            "wallet": metrics.wallet,
            "blocks": metrics.blocks,
            "initial_capital": float(metrics.initial_capital),
            "final_capital": float(metrics.final_capital),
            "net_profit": float(metrics.net_profit),
            "twr_pct": float(metrics.twr),
            "apr_pct": float(metrics.apr),
            "apy_pct": float(metrics.apy),
            "divergence_loss": float(metrics.divergence_loss),
            "vs_hodl": float(metrics.vs_hodl),
            "hodl_apr_pct": float(metrics.hodl_apr),
            "vs_hodl_apr_pct": float(metrics.vs_hodl_apr),
            "rebalance_count": metrics.rebalance_count,
            "days_active": float(metrics.days_active),
            "btc_price_start": float(metrics.btc_price_start),
            "btc_price_end": float(metrics.btc_price_end),
            "xirr_pct": float(metrics.xirr) if metrics.xirr is not None else None,
            "can_calculate_xirr": metrics.xirr is not None,
            "xirr_note": "XIRR converged successfully" if metrics.xirr is not None else f"Failed to converge with {metrics.rebalance_count} rebalances"
        }
        print(json.dumps(output, indent=2))

    elif args.format == 'text':
        print("="*60)
        print("AERODROME LP PERFORMANCE ANALYSIS")
        print("="*60)
        print(f"Wallet: {metrics.wallet}")
        print(f"Blocks: {metrics.blocks}")
        print(f"\nBTC Price Movement:")
        print(f"  Start: ${metrics.btc_price_start:,.2f}")
        print(f"  End: ${metrics.btc_price_end:,.2f}")
        change = float(metrics.btc_price_end / (metrics.btc_price_start if metrics.btc_price_start != 0 else Decimal('1')) - 1) * 100.0
        print(f"  Change: {change:.1f}%")
        print(f"\nCapital:")
        print(f"  Initial: ${metrics.initial_capital:,.2f}")
        print(f"  Final: ${metrics.final_capital:,.2f}")
        print(f"  Net Profit: ${metrics.net_profit:,.2f}")
        print(f"\nActivity:")
        print(f"  Days Active: {metrics.days_active:.2f}")
        print(f"  Rebalances: {metrics.rebalance_count}")
        if metrics.rebalance_count > 0:
            freq = float(metrics.days_active) / metrics.rebalance_count
            print(f"  Frequency: Every {freq:.1f} days")
        else:
            print(f"  Frequency: No rebalances")
        print(f"\nReturns:")
        if metrics.xirr is not None:
            print(f"  XIRR: {metrics.xirr:.2f}%")
        else:
            print(f"  XIRR: Failed to converge")
        print(f"  TWR: {metrics.twr:.2f}%")
        print(f"  LP APR: {metrics.apr:.2f}%")
        print(f"  LP APY: {metrics.apy:.2f}%")
        print(f"  HODL APR: {metrics.hodl_apr:.2f}%")
        print(f"  Outperformance: {metrics.vs_hodl_apr:+.2f}% APR")
        print(f"\nRisk Metrics:")
        print(f"  Divergence Loss: ${metrics.divergence_loss:,.2f}")
        print(f"  vs HODL: ${metrics.vs_hodl:+,.2f}")
        if metrics.vs_hodl > 0:
            print(f"\n✅ LP outperformed HODL by ${metrics.vs_hodl:,.2f}")
        else:
            print(f"\n❌ LP underperformed HODL by ${abs(metrics.vs_hodl):,.2f}")

    else:
        print(f"""
EXECUTIVE SUMMARY
================
Wallet: {metrics.wallet}
Blocks: {metrics.blocks}
Period: {metrics.days_active:.2f} days

Capital: ${metrics.initial_capital:,.0f} → +${metrics.net_profit:,.0f} profit
Return: {float(metrics.net_profit/metrics.initial_capital*100):.0f}% ({metrics.apr:.0f}% APR)
Activity: {metrics.rebalance_count} rebalances
vs HODL: {'+$' + str(int(metrics.vs_hodl)) if metrics.vs_hodl > 0 else '-$' + str(int(abs(metrics.vs_hodl)))}

BTC moved: ${metrics.btc_price_start:,.0f} → ${metrics.btc_price_end:,.0f} (+{((metrics.btc_price_end/metrics.btc_price_start - 1) * 100):.0f}%)

Returns:
  {'XIRR: ' + str(int(metrics.xirr)) + '%' if metrics.xirr is not None else 'XIRR: Failed to converge'}
  LP APR: {metrics.apr:.0f}%
  HODL APR: {metrics.hodl_apr:.0f}%
  Outperformance: {metrics.vs_hodl_apr:+.0f}% APR

Result: {'✅ Beat HODL by $' + str(int(metrics.vs_hodl)) if metrics.vs_hodl > 0 else '❌ Lost to HODL by $' + str(int(abs(metrics.vs_hodl)))}
""")

    return 0

if __name__ == "__main__":
    sys.exit(main())
