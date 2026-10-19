from decimal import Decimal

class FeesIdsService:
    COINGECKO_IDS = {
        # === Топ-10 по капитализации ===
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "USDT": "tether",
        "BNB": "binancecoin",
        "SOL": "solana",
        "XRP": "ripple",
        "USDC": "usd-coin",
        "ADA": "cardano",
        "DOGE": "dogecoin",
        "TRX": "tron",
        
        # === EVM-сети (Layer 2) ===
        "MATIC": "matic-network",
        "AVAX": "avalanche-2",
        "ARB": "arbitrum",
        "OP": "optimism",
        "BASE": "base-protocol",
        
        # === Мемкоины ===
        "SHIB": "shiba-inu",
        "PEPE": "pepe",
        "WIF": "dogwifcoin",
        "BONK": "bonk",
        
        # === DeFi-токены ===
        "UNI": "uniswap",
        "AAVE": "aave",
        "MKR": "maker",
        "LINK": "chainlink",
        "CRV": "curve-dao-token",
        "COMP": "compound-governance-token",
        
        # === Другие популярные ===
        "LTC": "litecoin",
        "DOT": "polkadot",
        "ATOM": "cosmos",
        "NEAR": "near",
        "APT": "aptos",
        "SUI": "sui",
        "TON": "the-open-network",
        "ETC": "ethereum-classic",
        "BCH": "bitcoin-cash",
        "XLM": "stellar",
        "ALGO": "algorand",
        "FIL": "filecoin",
        
        # === Стейблкоины ===
        "DAI": "dai",
        "TUSD": "true-usd",
        "BUSD": "binance-usd",
        "FDUSD": "first-digital-usd",
    }

    GAS_FEES = {
            # ==========================================
            # ТОП-10 ПО КАПИТАЛИЗАЦИИ
            # ==========================================
            "bitcoin": Decimal("0.0001"),           # BTC   ~$6
            "ethereum": Decimal("0.0005"),          # ETH   ~$1.5
            "tether": Decimal("1.0"),               # USDT  ~$1
            "binancecoin": Decimal("0.0001"),       # BNB   ~$0.05
            "solana": Decimal("0.00005"),           # SOL   ~$0.0075
            "ripple": Decimal("0.0001"),            # XRP   ~$0.0001
            "usd-coin": Decimal("1.0"),             # USDC  ~$1
            "cardano": Decimal("0.1"),              # ADA   ~$0.05
            "dogecoin": Decimal("1.0"),             # DOGE  ~$0.1
            "tron": Decimal("1.0"),                 # TRX   ~$0.1

            # ==========================================
            # EVM-СЕТИ И LAYER 2
            # ==========================================
            "matic-network": Decimal("0.01"),       # MATIC ~$0.005
            "avalanche-2": Decimal("0.001"),        # AVAX  ~$0.03
            "arbitrum": Decimal("0.0001"),          # ARB   ~$0.0001
            "optimism": Decimal("0.0001"),          # OP    ~$0.0001
            "base-protocol": Decimal("0.0001"),     # BASE  ~$0.0001

            # ==========================================
            # МЕМКОИНЫ
            # ==========================================
            "shiba-inu": Decimal("100000"),         # SHIB  ~$2.5
            "pepe": Decimal("100000"),              # PEPE  ~$1
            "dogwifcoin": Decimal("1.0"),           # WIF   ~$2
            "bonk": Decimal("10000"),               # BONK  ~$0.2

            # ==========================================
            # DEFI-ТОКЕНЫ
            # ==========================================
            "uniswap": Decimal("0.01"),             # UNI   ~$0.1
            "aave": Decimal("0.001"),               # AAVE  ~$0.1
            "maker": Decimal("0.0001"),             # MKR   ~$0.2
            "chainlink": Decimal("0.01"),           # LINK  ~$0.15
            "curve-dao-token": Decimal("0.1"),      # CRV   ~$0.05
            "compound-governance-token": Decimal("0.01"), # COMP ~$0.5

            # ==========================================
            # ДРУГИЕ ПОПУЛЯРНЫЕ
            # ==========================================
            "litecoin": Decimal("0.001"),           # LTC   ~$0.08
            "polkadot": Decimal("0.01"),            # DOT   ~$0.05
            "cosmos": Decimal("0.01"),              # ATOM  ~$0.05
            "near": Decimal("0.01"),                # NEAR  ~$0.05
            "aptos": Decimal("0.001"),              # APT   ~$0.01
            "sui": Decimal("0.01"),                 # SUI   ~$0.02
            "the-open-network": Decimal("0.01"),    # TON   ~$0.05
            "ethereum-classic": Decimal("0.001"),   # ETC   ~$0.02
            "bitcoin-cash": Decimal("0.0001"),      # BCH   ~$0.03
            "stellar": Decimal("0.1"),              # XLM   ~$0.01
            "algorand": Decimal("0.1"),             # ALGO  ~$0.02
            "filecoin": Decimal("0.01"),            # FIL   ~$0.05

            # ==========================================
            # СТЕЙБЛКОИНЫ
            # ==========================================
            "dai": Decimal("1.0"),                  # DAI   ~$1
            "true-usd": Decimal("1.0"),             # TUSD  ~$1
            "binance-usd": Decimal("1.0"),          # BUSD  ~$1
            "first-digital-usd": Decimal("1.0"),    # FDUSD ~$1
        }

feesservice = FeesIdsService()