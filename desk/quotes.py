"""Jupiter V2 quote-only checks. No taker, signer, or execute endpoint."""
import math
import os
from datetime import datetime
from urllib.parse import urlencode

from .core import money
from .http import JsonHTTP
from .onchain import address, uint

USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
WRAPPED_SOL = 'So11111111111111111111111111111111111111112'


def finite(value):
    if isinstance(value, bool):
        raise ValueError('invalid quote number')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite quote number')
    return result


def parse_quote(data, input_mint, output_mint, amount, observed_at):
    if not isinstance(data, dict) or data.get('error') or data.get('errorCode') is not None:
        raise ValueError('Jupiter returned an order error')
    if data.get('transaction') is not None or data.get('taker'):
        raise ValueError('expected quote-only response without a transaction')
    if data.get('inputMint') != input_mint or data.get('outputMint') != output_mint or uint(data.get('inAmount')) != amount:
        raise ValueError('quote identity or input amount mismatch')
    if data.get('swapMode') != 'ExactIn':
        raise ValueError('only ExactIn quotes are supported')
    output = uint(data['outAmount'])
    minimum = uint(data['otherAmountThreshold'])
    if not 0 < minimum <= output or not data.get('routePlan') or data.get('router') not in ('metis','jupiterz','dflow','okx'):
        raise ValueError('missing route or invalid output bounds')
    slippage = uint(data['slippageBps'])
    impact = abs(finite(data['priceImpact']))  # V2 percentage points, not deprecated priceImpactPct.
    fee = finite(data['feeBps'])
    if not 0 <= fee <= 10000 or data.get('feeMint') not in (input_mint, output_mint):
        raise ValueError('invalid quote fee evidence')
    if slippage > 10000 or minimum < output*(10000-slippage)//10000:
        raise ValueError('quote minimum inconsistent with slippage')
    network = sum(uint(data[key]) for key in ('signatureFeeLamports','prioritizationFeeLamports','rentFeeLamports'))
    expiry = None
    if data.get('expireAt'):
        expiry = datetime.fromisoformat(data['expireAt'].replace('Z','+00:00'))
        if expiry.tzinfo is None:
            raise ValueError('quote expiry lacks timezone')
        expiry = expiry.timestamp()
        if expiry <= observed_at:
            raise ValueError('expired RFQ quote')
    return dict(input_mint=input_mint, output_mint=output_mint, input_amount=str(amount),
                output_amount=str(output), minimum_output=str(minimum), observed_at=observed_at,
                price_impact_pct=impact, fee_bps=fee, slippage_bps=slippage,
                network_lamports=network, router=data['router'], expire_at=expiry,
                quote_only=True, wallet_costs_verified=False)


class Jupiter:
    base = 'https://api.jup.ag/swap/v2/order'

    def __init__(self, recorder, http=None):
        self.recorder = recorder
        self.http = http or JsonHTTP(spacing=2.1)
        self.key = os.environ.get('JUPITER_API_KEY')

    def quote(self, input_mint, output_mint, amount, slippage_bps):
        address(input_mint)
        address(output_mint)
        if amount <= 0 or not isinstance(amount, int):
            raise ValueError('quote amount must be a positive integer')
        uint(amount)
        params = dict(inputMint=input_mint, outputMint=output_mint, amount=str(amount),
                      swapMode='ExactIn', slippageBps=slippage_bps)
        headers = {'x-api-key':self.key} if self.key else {}
        data, stamp = self.http.request(self.base+'?'+urlencode(params), headers=headers)
        self.recorder('jupiter:order', data, stamp)
        return parse_quote(data, input_mint, output_mint, amount, stamp)

    def roundtrip(self, token, ticket_usdc, slippage_bps):
        if token == USDC:
            raise ValueError('candidate must differ from USDC quote currency')
        atomic = money(ticket_usdc)*1000000
        if atomic != atomic.to_integral_value() or atomic <= 0:
            raise ValueError('USDC ticket must be positive with at most six decimals')
        buy = self.quote(USDC, token, int(atomic), slippage_bps)
        # Test sale of the conservative buy quantity, not an optimistic expected output.
        sell = self.quote(token, USDC, int(buy['minimum_output']), slippage_bps)
        loss = (1-money(sell['minimum_output'])/money(buy['input_amount']))*100
        return dict(token=token, quote_currency=USDC, ticket_usdc=str(ticket_usdc), buy=buy, sell=sell,
                    worst_quote_roundtrip_loss_pct=float(loss),
                    network_lamports=buy['network_lamports']+sell['network_lamports'],
                    network_costs_included_in_loss=False, execution_verified=False)
