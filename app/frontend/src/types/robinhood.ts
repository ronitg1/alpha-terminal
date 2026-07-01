export interface RobinhoodToolPayload {
  readonly tool: string;
  readonly data: unknown;
}

export interface RobinhoodPortfolioResponse {
  readonly status: 'ok';
  readonly endpoint: string;
  readonly asof: string;
  readonly tools: readonly RobinhoodToolPayload[];
}
