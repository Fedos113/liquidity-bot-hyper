
# HyperEVM RPC Providers - Complete Setup Guide

## Table of Contents
1. [Alchemy](#alchemy)
2. [Chainstack](#chainstack)
3. [dRPC](#drpc)
4. [HypeRPC](#hypercpc)
5. [Comparison Table](#comparison-table)

---

## Alchemy

### Overview
- **Free Tier**: 25 RPS, 30M CU/month
- **Paid Tiers**: Starting at $49/month
- **CU Pricing**: $0.40-0.45 per 1M CU
- **Website**: https://www.alchemy.com/hyperevm

### API Endpoints

#### Mainnet
```
https://eth-hyperliquid-mainnet.g.alchemy.com/v2/YOUR_API_KEY
```

#### Testnet
```
https://eth-hyperliquid-testnet.g.alchemy.com/v2/YOUR_API_KEY
```

### Supported RPC Methods

#### Read Methods
- `eth_blockNumber` - 10 CU
- `eth_getBalance` - 26 CU
- `eth_getTransactionCount` - 26 CU
- `eth_getCode` - 26 CU
- `eth_call` - 26 CU
- `eth_estimateGas` - 26 CU
- `eth_gasPrice` - 0 CU
- `eth_maxPriorityFeePerGas` - 0 CU
- `eth_getTransactionByHash` - 26 CU
- `eth_getTransactionReceipt` - 26 CU
- `eth_getLogs` - 60 CU
- `eth_getStorageAt` - 26 CU
- `net_version` - 0 CU
- `eth_chainId` - 0 CU

#### Write Methods
- `eth_sendRawTransaction` - 40 CU
- `eth_subscribe` (WebSocket) - Variable CU

### Python Setup Guide

#### Installation
```bash
pip install web3 eth-account
```

#### Basic Configuration
```python
from web3 import Web3
import os

# Configuration
ALCHEMY_API_KEY = "your_api_key_here"
ALCHEMY_URL = f"https://eth-hyperliquid-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(ALCHEMY_URL))

# Verify connection
print(f"Connected: {w3.is_connected()}")
print(f"Chain ID: {w3.eth.chain_id}")
print(f"Latest Block: {w3.eth.block_number}")
```

#### Advanced Configuration with Retry
```python
from web3 import Web3
from web3.middleware import geth_poa_middleware
import time

class AlchemyProvider:
    def __init__(self, api_key, max_retries=3, timeout=30):
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self.url = f"https://eth-hyperliquid-mainnet.g.alchemy.com/v2/{api_key}"
        self.w3 = None
        self.connect()
    
    def connect(self):
        """Initialize Web3 connection with retry logic"""
        for attempt in range(self.max_retries):
            try:
                self.w3 = Web3(Web3.HTTPProvider(
                    self.url,
                    request_kwargs={'timeout': self.timeout}
                ))
                
                # Add middleware for PoA chains if needed
                # self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                
                if self.w3.is_connected():
                    print(f"✓ Connected to Alchemy HyperEVM")
                    print(f"  Chain ID: {self.w3.eth.chain_id}")
                    return
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {e}")
                time.sleep(2 ** attempt)  # Exponential backoff
        
        raise ConnectionError("Failed to connect to Alchemy after all retries")
    
    def get_balance(self, address):
        """Get balance in ETH"""
        balance_wei = self.w3.eth.get_balance(address)
        return self.w3.from_wei(balance_wei, 'ether')
    
    def get_nonce(self, address):
        """Get transaction count for address"""
        return self.w3.eth.get_transaction_count(address, 'pending')
    
    def send_transaction(self, signed_tx):
        """Broadcast signed transaction"""
        return self.w3.eth.send_raw_transaction(signed_tx)
    
    def estimate_gas(self, transaction):
        """Estimate gas for transaction"""
        return self.w3.eth.estimate_gas(transaction)
    
    def get_gas_price(self):
        """Get current gas price"""
        return self.w3.eth.gas_price
    
    def get_max_priority_fee(self):
        """Get max priority fee per gas"""
        return self.w3.eth.max_priority_fee
```

#### Using Alchemy Enhanced APIs
```python
import requests
import json

class AlchemyEnhancedAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = f"https://eth-hyperliquid-mainnet.g.alchemy.com/v2/{api_key}"
        self.headers = {"Content-Type": "application/json"}
    
    def rpc_call(self, method, params=None):
        """Make raw RPC call"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1
        }
        
        response = requests.post(
            self.base_url,
            headers=self.headers,
            data=json.dumps(payload)
        )
        
        return response.json()
    
    def get_token_balances(self, address):
        """Get all token balances for address (Alchemy enhanced)"""
        return self.rpc_call("alchemy_getTokenBalances", [address])
    
    def get_asset_transfers(self, address, from_block="0x0"):
        """Get asset transfers for address"""
        return self.rpc_call("alchemy_getAssetTransfers", [{
            "fromBlock": from_block,
            "toBlock": "latest",
            "fromAddress": address,
            "category": ["external", "internal", "erc20", "erc721"]
        }])
```

#### Multicall Implementation
```python
from web3 import Web3
from web3.contract import Contract

# Multicall3 ABI (minimal)
MULTICALL3_ABI = [{
    "inputs": [{
        "components": [{
            "internalType": "address",
            "name": "target",
            "type": "address"
        }, {
            "internalType": "bytes",
            "name": "callData",
            "type": "bytes"
        }],
        "internalType": "struct Multicall3.Call[]",
        "name": "calls",
        "type": "tuple[]"
    }],
    "name": "aggregate",
    "outputs": [{
        "internalType": "uint256",
        "name": "blockNumber",
        "type": "uint256"
    }, {
        "internalType": "bytes[]",
        "name": "returnData",
        "type": "bytes[]"
    }],
    "stateMutability": "payable",
    "type": "function"
}]

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

class AlchemyMulticall:
    def __init__(self, alchemy_provider):
        self.w3 = alchemy_provider.w3
        self.multicall = self.w3.eth.contract(
            address=MULTICALL3_ADDRESS,
            abi=MULTICALL3_ABI
        )
    
    def aggregate(self, calls):
        """
        Execute multiple calls in one RPC request
        
        Args:
            calls: List of tuples (target_address, call_data)
        
        Returns:
            block_number, return_data
        """
        return self.multicall.functions.aggregate(calls).call()
    
    def get_token_balance(self, token_address, wallet_address):
        """Create call data for token balance"""
        token_contract = self.w3.eth.contract(
            address=token_address,
            abi=[{
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function"
            }]
        )
        return token_contract.encodeABI(fn_name="balanceOf", args=[wallet_address])
```

---

## Chainstack

### Overview
- **Free Tier**: 3M RU/month
- **Paid Tiers**: $49-499/month
- **RU Pricing**: ~$4.95 per 1M RU (Growth tier)
- **Website**: https://chainstack.com/hyperliquid-rpc-node/

### API Endpoints

#### HTTP Endpoint (after deployment)
```
https://your-node-id.hyperliquid-mainnet.chainstack.com
```

#### WebSocket Endpoint
```
wss://your-node-id.hyperliquid-mainnet.chainstack.com
```

### Supported RPC Methods

Standard Ethereum JSON-RPC methods:
- `eth_blockNumber`
- `eth_getBalance`
- `eth_getTransactionCount`
- `eth_call`
- `eth_sendRawTransaction`
- `eth_getLogs`
- `eth_estimateGas`
- `eth_gasPrice`
- All standard Ethereum methods

### Python Setup Guide

#### Installation
```bash
pip install web3 requests
```

#### Basic Configuration
```python
from web3 import Web3
import os

# Configuration
CHAINSTACK_ENDPOINT = "https://your-node-id.hyperliquid-mainnet.chainstack.com"
CHAINSTACK_API_KEY = "your_api_key_if_required"  # Optional

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(CHAINSTACK_ENDPOINT))

# Verify connection
print(f"Connected: {w3.is_connected()}")
print(f"Chain ID: {w3.eth.chain_id}")
```

#### Advanced Configuration
```python
from web3 import Web3
from web3.middleware import geth_poa_middleware
import time
import requests

class ChainstackProvider:
    def __init__(self, endpoint, api_key=None, max_retries=3):
        self.endpoint = endpoint
        self.api_key = api_key
        self.max_retries = max_retries
        self.headers = {
            "Content-Type": "application/json"
        }
        
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        
        self.w3 = None
        self.connect()
    
    def connect(self):
        """Initialize connection with retry"""
        for attempt in range(self.max_retries):
            try:
                self.w3 = Web3(Web3.HTTPProvider(
                    self.endpoint,
                    request_kwargs={
                        'timeout': 30,
                        'headers': self.headers
                    }
                ))
                
                if self.w3.is_connected():
                    print(f"✓ Connected to Chainstack HyperEVM")
                    print(f"  Chain ID: {self.w3.eth.chain_id}")
                    return
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {e}")
                time.sleep(2 ** attempt)
        
        raise ConnectionError("Failed to connect to Chainstack")
    
    def rpc_call(self, method, params=None):
        """Make direct RPC call"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1
        }
        
        response = requests.post(
            self.endpoint,
            headers=self.headers,
            json=payload
        )
        
        return response.json()
    
    def get_block_info(self, block_number="latest"):
        """Get block information"""
        return self.rpc_call("eth_getBlockByNumber", [block_number, True])
    
    def get_transaction_receipt(self, tx_hash):
        """Get transaction receipt"""
        return self.rpc_call("eth_getTransactionReceipt", [tx_hash])
```

#### WebSocket Connection
```python
from web3 import Web3
import asyncio

class ChainstackWebSocket:
    def __init__(self, ws_endpoint):
        self.ws_endpoint = ws_endpoint
        self.w3 = None
    
    def connect(self):
        """Connect via WebSocket"""
        self.w3 = Web3(Web3.WebsocketProvider(self.ws_endpoint))
        
        if self.w3.is_connected():
            print("✓ Connected to Chainstack WebSocket")
            return True
        return False
    
    def subscribe_new_heads(self, callback):
        """Subscribe to new block headers"""
        subscription = self.w3.eth.subscribe('newHeads')
        
        for event in subscription:
            callback(event)
    
    def subscribe_logs(self, address, callback):
        """Subscribe to logs from specific address"""
        subscription = self.w3.eth.subscribe('logs', {
            'address': address
        })
        
        for event in subscription:
            callback(event)
```

---

## dRPC

### Overview
- **Free Tier**: 210M CU/month (public nodes)
- **Paid Tiers**: $10-299/month
- **CU Pricing**: $0.30 per 1M CU (flat rate)
- **Website**: https://drpc.org/chainlist/hyperliquid-mainnet-rpc

### API Endpoints

#### Mainnet
```
https://lb.drpc.org/ogrpc?network=hyperliquid&dkey=YOUR_API_KEY
```

#### WebSocket
```
wss://lb.drpc.org/ogrpc?network=hyperliquid&dkey=YOUR_API_KEY
```

### Supported RPC Methods

All standard Ethereum methods with flat 20 CU pricing:
- `eth_blockNumber` - 20 CU
- `eth_getBalance` - 20 CU
- `eth_call` - 20 CU
- `eth_sendRawTransaction` - 20 CU
- `eth_getLogs` - 20 CU
- All methods: 20 CU flat rate

### Python Setup Guide

#### Installation
```bash
pip install web3
```

#### Basic Configuration
```python
from web3 import Web3

# Configuration
DRPC_API_KEY = "your_api_key_here"
DRPC_URL = f"https://lb.drpc.org/ogrpc?network=hyperliquid&dkey={DRPC_API_KEY}"

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(DRPC_URL))

# Verify connection
print(f"Connected: {w3.is_connected()}")
print(f"Chain ID: {w3.eth.chain_id}")
```

#### Advanced Configuration with Load Balancing
```python
from web3 import Web3
import random
import time

class dRPCProvider:
    def __init__(self, api_key, use_fallback=True):
        self.api_key = api_key
        self.use_fallback = use_fallback
        self.endpoints = [
            f"https://lb.drpc.org/ogrpc?network=hyperliquid&dkey={api_key}",
            # Add multiple endpoints for load balancing if needed
        ]
        self.current_endpoint = None
        self.w3 = None
        self.connect()
    
    def connect(self):
        """Connect to dRPC with automatic failover"""
        for endpoint in self.endpoints:
            try:
                self.w3 = Web3(Web3.HTTPProvider(
                    endpoint,
                    request_kwargs={'timeout': 30}
                ))
                
                if self.w3.is_connected():
                    self.current_endpoint = endpoint
                    print(f"✓ Connected to dRPC HyperEVM")
                    print(f"  Endpoint: {endpoint[:50]}...")
                    return
            except Exception as e:
                print(f"Failed to connect to {endpoint}: {e}")
                continue
        
        raise ConnectionError("Failed to connect to any dRPC endpoint")
    
    def get_compute_units_used(self):
        """Get CU usage from dRPC dashboard API"""
        import requests
        
        response = requests.get(
            "https://drpc.org/api/usage",
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        
        return response.json()
    
    def estimate_cost(self, num_calls):
        """Estimate cost for number of calls"""
        # dRPC charges 20 CU per call
        total_cu = num_calls * 20
        cost_usd = (total_cu / 1_000_000) * 0.30
        return {
            "compute_units": total_cu,
            "estimated_cost_usd": cost_usd
        }
```

#### Batch Requests
```python
import requests
import json

class dRPCBatchClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.url = f"https://lb.drpc.org/ogrpc?network=hyperliquid&dkey={api_key}"
        self.headers = {"Content-Type": "application/json"}
    
    def batch_call(self, calls):
        """
        Execute multiple RPC calls in one HTTP request
        
        Args:
            calls: List of tuples (method, params)
        
        Returns:
            List of responses
        """
        payload = []
        
        for i, (method, params) in enumerate(calls):
            payload.append({
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": i
            })
        
        response = requests.post(
            self.url,
            headers=self.headers,
            data=json.dumps(payload)
        )
        
        return response.json()
    
    def get_multiple_balances(self, addresses):
        """Get balances for multiple addresses in one request"""
        calls = [
            ("eth_getBalance", [addr, "latest"])
            for addr in addresses
        ]
        
        return self.batch_call(calls)
```

---

## HypeRPC

### Overview
- **Free Tier**: 2M CU/month, 100 CU/s
- **Paid Tiers**: $99-499/month
- **CU Pricing**: $0.50 per 1M CU (EU), $0.75 per 1M CU (JP)
- **Website**: https://hypercpc.app
- **Special Feature**: Sub-millisecond latency with Japan validator peering

### API Endpoints

#### Mainnet EVM
```
https://rpc.hyperpc.app/evm?api_key=YOUR_API_KEY
```

#### Mainnet HyperCore (L1)
```
https://rpc.hyperpc.app/hypercore?api_key=YOUR_API_KEY
```

#### WebSocket
```
wss://rpc.hyperpc.app/evm?api_key=YOUR_API_KEY
```

### Supported RPC Methods

#### EVM Methods
- `eth_blockNumber`
- `eth_getBalance`
- `eth_getTransactionCount`
- `eth_call`
- `eth_sendRawTransaction`
- `eth_getLogs`
- `eth_estimateGas`
- All standard Ethereum JSON-RPC methods

#### HyperCore Methods (L1)
- `getInfo` - Get chain info
- `getRecentTrades` - Get recent trades
- `getL2Book` - Get order book
- `getUserFills` - Get user fills
- `getOpenOrders` - Get open orders
- All Hyperliquid L1 API methods

### Python Setup Guide

#### Installation
```bash
pip install web3 requests
```

#### Basic Configuration
```python
from web3 import Web3

# Configuration
HYPERPC_API_KEY = "your_api_key_here"
HYPERPC_EVM_URL = f"https://rpc.hyperpc.app/evm?api_key={HYPERPC_API_KEY}"

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(HYPERPC_EVM_URL))

# Verify connection
print(f"Connected: {w3.is_connected()}")
print(f"Chain ID: {w3.eth.chain_id}")
```

#### Advanced Configuration with HyperCore Integration
```python
from web3 import Web3
import requests
import time

class HypeRPCProvider:
    def __init__(self, api_key, region="eu"):
        self.api_key = api_key
        self.region = region  # "eu" or "jp"
        
        # EVM endpoints
        self.evm_url = f"https://rpc.hyperpc.app/evm?api_key={api_key}"
        
        # HyperCore (L1) endpoints
        self.hypercore_url = f"https://rpc.hyperpc.app/hypercore?api_key={api_key}"
        
        self.w3 = None
        self.headers = {"Content-Type": "application/json"}
        
        self.connect()
    
    def connect(self):
        """Connect to HypeRPC EVM"""
        self.w3 = Web3(Web3.HTTPProvider(
            self.evm_url,
            request_kwargs={'timeout': 30}
        ))
        
        if self.w3.is_connected():
            print(f"✓ Connected to HypeRPC HyperEVM")
            print(f"  Region: {self.region.upper()}")
            print(f"  Chain ID: {self.w3.eth.chain_id}")
        else:
            raise ConnectionError("Failed to connect to HypeRPC")
    
    # EVM Methods
    def get_balance(self, address):
        """Get balance in HYPE"""
        balance_wei = self.w3.eth.get_balance(address)
        return self.w3.from_wei(balance_wei, 'ether')
    
    def send_transaction(self, signed_tx):
        """Broadcast transaction"""
        return self.w3.eth.send_raw_transaction(signed_tx)
    
    # HyperCore (L1) Methods
    def hypercore_call(self, method, params=None):
        """Make HyperCore API call"""
        payload = {
            "method": method,
            "params": params or {},
        }
        
        response = requests.post(
            self.hypercore_url,
            headers=self.headers,
            json=payload
        )
        
        return response.json()
    
    def get_order_book(self, coin):
        """Get L2 order book for a coin"""
        return self.hypercore_call("getL2Book", {"coin": coin})
    
    def get_recent_trades(self, coin):
        """Get recent trades for a coin"""
        return self.hypercore_call("getRecentTrades", {"coin": coin})
    
    def get_user_fills(self, user, startTime=None, endTime=None):
        """Get user fills"""
        params = {"user": user}
        if startTime:
            params["startTime"] = startTime
        if endTime:
            params["endTime"] = endTime
        
        return self.hypercore_call("getUserFills", params)
    
    def get_open_orders(self, user):
        """Get user's open orders"""
        return self.hypercore_call("getOpenOrders", {"user": user})
    
    def get_info(self):
        """Get chain info"""
        return self.hypercore_call("getInfo")
    
    # Latency Testing
    def test_latency(self, num_tests=10):
        """Test RPC latency"""
        latencies = []
        
        for i in range(num_tests):
            start = time.time()
            self.w3.eth.block_number
            end = time.time()
            
            latency_ms = (end - start) * 1000
            latencies.append(latency_ms)
        
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        print(f"\nLatency Test Results ({num_tests} requests):")
        print(f"  Average: {avg_latency:.2f}ms")
        print(f"  Min: {min_latency:.2f}ms")
        print(f"  Max: {max_latency:.2f}ms")
        
        return {
            "average_ms": avg_latency,
            "min_ms": min_latency,
            "max_ms": max_latency
        }
```

#### WebSocket Subscription
```python
from web3 import Web3
import json

class HypeRPCWebSocket:
    def __init__(self, api_key):
        self.api_key = api_key
        self.ws_url = f"wss://rpc.hyperpc.app/evm?api_key={api_key}"
        self.w3 = None
    
    def connect(self):
        """Connect via WebSocket"""
        self.w3 = Web3(Web3.WebsocketProvider(self.ws_url))
        
        if self.w3.is_connected():
            print("✓ Connected to HypeRPC WebSocket")
            return True
        return False
    
    def subscribe_new_blocks(self, callback):
        """Subscribe to new blocks"""
        subscription = self.w3.eth.subscribe('newHeads')
        
        print("Listening for new blocks...")
        for event in subscription:
            callback(event)
    
    def subscribe_pending_transactions(self, callback):
        """Subscribe to pending transactions"""
        subscription = self.w3.eth.subscribe('pendingTransactions')
        
        print("Listening for pending transactions...")
        for tx_hash in subscription:
            callback(tx_hash)
```

#### Multicall3 on HyperEVM
```python
from web3 import Web3

class HypeRPCMulticall:
    """
    Multicall3 is deployed at 0x0000000000000000000000000000000000000999
    on HyperEVM
    """
    
    MULTICALL3_ADDRESS = "0x0000000000000000000000000000000000000999"
    
    MULTICALL3_ABI = [{
        "inputs": [{
            "components": [{
                "internalType": "address",
                "name": "target",
                "type": "address"
            }, {
                "internalType": "bytes",
                "name": "callData",
                "type": "bytes"
            }],
            "internalType": "struct Multicall3.Call[]",
            "name": "calls",
            "type": "tuple[]"
        }],
        "name": "aggregate",
        "outputs": [{
            "internalType": "uint256",
            "name": "blockNumber",
            "type": "uint256"
        }, {
            "internalType": "bytes[]",
            "name": "returnData",
            "type": "bytes[]"
        }],
        "stateMutability": "payable",
        "type": "function"
    }]
    
    def __init__(self, hyrpc_provider):
        self.w3 = hyrpc_provider.w3
        self.multicall = self.w3.eth.contract(
            address=self.MULTICALL3_ADDRESS,
            abi=self.MULTICALL3_ABI
        )
    
    def aggregate(self, calls):
        """
        Execute multiple contract calls in one RPC request
        
        Args:
            calls: List of tuples (target_address, call_data)
        
        Returns:
            block_number, return_data
        """
        return self.multicall.functions.aggregate(calls).call()
    
    def check_pool_position(self, pool_address, user_address):
        """Create call to check liquidity position"""
        position_manager_abi = [{
            "inputs": [
                {"internalType": "uint256", "name": "tokenId", "type": "uint256"}
            ],
            "name": "positions",
            "outputs": [
                {"internalType": "uint96", "name": "nonce", "type": "uint96"},
                {"internalType": "address", "name": "operator", "type": "address"},
                {"internalType": "address", "name": "token0", "type": "address"},
                {"internalType": "address", "name": "token1", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "int24", "name": "tickLower", "type": "int24"},
                {"internalType": "int24", "name": "tickUpper", "type": "int24"},
                {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
                {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
                {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
                {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
                {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"}
            ],
            "stateMutability": "view",
            "type": "function"
        }]
        
        contract = self.w3.eth.contract(
            address=pool_address,
            abi=position_manager_abi
        )
        
        # This is a simplified example - you'd need actual token IDs
        return contract.encodeABI(fn_name="positions", args=[1])
```

---

## Comparison Table

| Feature | Alchemy | Chainstack | dRPC | HypeRPC |
|---------|---------|------------|------|---------|
| **Free Tier** | 30M CU/month | 3M RU/month | 210M CU/month | 2M CU/month |
| **Free RPS** | 25 RPS | Variable | 100 RPS | 100 CU/s |
| **Paid Starting** | $49/month | $49/month | $10/month | $99/month |
| **CU/RU Cost** | $0.40-0.45/M | ~$4.95/M | $0.30/M | $0.50-0.75/M |
| **eth_sendRawTransaction** | 40 CU | Variable | 20 CU | Variable |
| **Flat Rate** | No | No | Yes (20 CU) | No |
| **WebSocket** | Yes | Yes | Yes | Yes |
| **Multicall Support** | Yes | Yes | Yes | Yes (0x999) |
| **HyperCore API** | No | No | No | Yes |
| **Japan Peering** | No | No | No | Yes |
| **Avg Latency** | ~50-100ms | ~30ms | ~32ms | <1ms (JP) |
| **Best For** | Dev tools | Reliability | Cost efficiency | Speed/HFT |

---

## Resources

- **HyperEVM Documentation**: https://docs.hyperliquid.xyz
- **Multicall3 Contract**: 0x0000000000000000000000000000000000000999
- **Hyperliquid Explorer**: https://hyperevmscan.io
- **Gas Tracker**: https://hyperevmscan.io/gastracker

### Provider Documentation
- **Alchemy**: https://docs.alchemy.com/docs/hyperliquid
- **Chainstack**: https://docs.chainstack.com/docs/hyperliquid
- **dRPC**: https://docs.drpc.org/
- **HypeRPC**: https://hypercpc.app/docs

---

*Last Updated: 2026*
*Chain ID: 999 (HyperEVM Mainnet)*