// Wire-format mirror of the SnapTrade backend routes (app/backend/routes/snaptrade.py).

export interface SnapTradeConnectionMeta {
  readonly snaptrade_user_id: string | null;
  readonly connected: boolean;
  readonly created_at: string | null;
  readonly updated_at: string | null;
}

export interface SnapTradeStatus {
  readonly configured: boolean;
  readonly connected: boolean;
  readonly approved: boolean;
  readonly connection: SnapTradeConnectionMeta | null;
}

export interface SnapTradeConnectResponse {
  readonly redirect_uri: string;
}

export interface SnapTradeStockPosition {
  readonly kind: 'stock';
  readonly symbol: string;
  readonly underlying: string;
  readonly units: number | null;
  readonly price: number | null;
  readonly market_value: number | null;
  readonly open_pnl: number | null;
  readonly raw: unknown;
}

export interface SnapTradeOptionPosition {
  readonly kind: 'option';
  readonly symbol: string;
  readonly underlying: string;
  readonly option_type: string | null;
  readonly strike: number | null;
  readonly expiration: string | null;
  readonly units: number | null;
  readonly price: number | null;
  readonly market_value: number | null;
  readonly raw: unknown;
}

export interface SnapTradeAccount {
  readonly id: string;
  readonly label: string;
  readonly name: string | null;
  readonly institution: string | null;
  readonly number: string | null;
  readonly positions: readonly SnapTradeStockPosition[];
  readonly options: readonly SnapTradeOptionPosition[];
}

export interface SnapTradePortfolioResponse {
  readonly status: 'ok';
  readonly accounts: readonly SnapTradeAccount[];
}
