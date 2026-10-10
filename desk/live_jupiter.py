"""Documented V1 quote/swap builder for inspectable V6 ExactIn routes."""
import os
from urllib.parse import urlencode

from .http import JsonHTTP
from .onchain import address, uint
from .quotes import finite


class LiveJupiter:
    base = 'https://api.jup.ag/swap/v1'

    def __init__(self, recorder, http=None):
        self.recorder = recorder
        self.http = http or JsonHTTP(spacing=2.1)
        key = os.environ.get('JUPITER_API_KEY')
        self.headers = {'x-api-key':key} if key else {}

    def build(self, wallet, input_mint, output_mint, amount, limits):
        for value in (wallet, input_mint, output_mint):
            address(value)
        params = dict(inputMint=input_mint, outputMint=output_mint, amount=str(uint(amount)),
                      swapMode='ExactIn', slippageBps=limits.slippage_bps,
                      instructionVersion='V1', restrictIntermediateTokens='true', maxAccounts=32)
        quote, stamp = self.http.request(self.base+'/quote?'+urlencode(params), headers=self.headers)
        self.recorder('jupiter_live:quote', quote, stamp)
        if (quote.get('error') or quote.get('inputMint') != input_mint or quote.get('outputMint') != output_mint
                or uint(quote.get('inAmount')) != amount or quote.get('swapMode') != 'ExactIn'
                or uint(quote.get('slippageBps')) != limits.slippage_bps or not quote.get('routePlan')):
            raise ValueError('live quote does not match intended swap')
        if abs(finite(quote['priceImpactPct']))*100 > limits.max_price_impact_pct:
            raise ValueError('live quote exceeds price impact limit')
        if quote.get('platformFee') is not None:
            if uint(quote['platformFee']['amount']) != 0 or uint(quote['platformFee']['feeBps']) != 0:
                raise ValueError('live pilot does not support platform/referral fees')
        output, minimum = uint(quote['outAmount']), uint(quote['otherAmountThreshold'])
        encoded_minimum = (output*(10000-limits.slippage_bps)+9999)//10000
        if not 0 < minimum <= output or minimum != encoded_minimum:
            raise ValueError('invalid live quote output bounds')
        order, built_at = self.http.request(self.base+'/swap', headers=self.headers, payload=dict(
            quoteResponse=quote, userPublicKey=wallet, wrapAndUnwrapSol=False,
            dynamicComputeUnitLimit=True, dynamicSlippage=False, asLegacyTransaction=False,
            prioritizationFeeLamports={'priorityLevelWithMaxLamports':{
                'maxLamports':limits.max_priority_fee_lamports,'priorityLevel':'medium','global':False}}))
        self.recorder('jupiter_live:build', order, built_at)
        if order.get('simulationError') is not None or not order.get('swapTransaction'):
            raise ValueError('Jupiter could not build and simulate the transaction')
        if uint(order['prioritizationFeeLamports']) > limits.max_priority_fee_lamports:
            raise ValueError('Jupiter priority fee exceeds configured cap')
        return dict(quote=quote, unsigned_transaction=order['swapTransaction'], observed_at=stamp,
                    built_at=built_at, last_valid_block_height=uint(order['lastValidBlockHeight']))
