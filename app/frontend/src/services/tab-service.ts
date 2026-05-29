import { Settings } from '@/components/settings/settings';
import { BacktestTab } from '@/components/sleeves/backtest/backtest-tab';
import { OptionsTab } from '@/components/sleeves/options/options-tab';
import { SleevesTab } from '@/components/sleeves/sleeves-tab';
import { StocksTab } from '@/components/stocks/stocks-tab';
import { FlowTabContent } from '@/components/tabs/flow-tab-content';
import { Flow } from '@/types/flow';
import { ReactNode, createElement } from 'react';

export interface TabData {
  type: 'flow' | 'settings' | 'sleeves' | 'options' | 'backtest' | 'stocks';
  title: string;
  flow?: Flow;
  metadata?: Record<string, any>;
}

export class TabService {
  static createTabContent(tabData: TabData): ReactNode {
    switch (tabData.type) {
      case 'flow':
        if (!tabData.flow) {
          throw new Error('Flow tab requires flow data');
        }
        return createElement(FlowTabContent, { flow: tabData.flow });

      case 'settings':
        return createElement(Settings);

      case 'sleeves':
        return createElement(SleevesTab);

      case 'options':
        return createElement(OptionsTab);

      case 'backtest':
        return createElement(BacktestTab);

      case 'stocks':
        return createElement(StocksTab);

      default:
        throw new Error(`Unsupported tab type: ${tabData.type}`);
    }
  }

  static createFlowTab(flow: Flow): TabData & { content: ReactNode } {
    return {
      type: 'flow',
      title: flow.name,
      flow: flow,
      content: TabService.createTabContent({ type: 'flow', title: flow.name, flow }),
    };
  }

  static createSettingsTab(): TabData & { content: ReactNode } {
    return {
      type: 'settings',
      title: 'Settings',
      content: TabService.createTabContent({ type: 'settings', title: 'Settings' }),
    };
  }

  static createSleevesTab(): TabData & { content: ReactNode } {
    return {
      type: 'sleeves',
      title: 'Sleeves',
      content: TabService.createTabContent({ type: 'sleeves', title: 'Sleeves' }),
    };
  }

  static createOptionsTab(): TabData & { content: ReactNode } {
    return {
      type: 'options',
      title: 'Options',
      content: TabService.createTabContent({ type: 'options', title: 'Options' }),
    };
  }

  static createBacktestTab(): TabData & { content: ReactNode } {
    return {
      type: 'backtest',
      title: 'Backtest',
      content: TabService.createTabContent({ type: 'backtest', title: 'Backtest' }),
    };
  }

  static createStocksTab(): TabData & { content: ReactNode } {
    return {
      type: 'stocks',
      title: 'My Stocks',
      content: TabService.createTabContent({ type: 'stocks', title: 'My Stocks' }),
    };
  }

  // Restore tab content for persisted tabs (used when loading from localStorage)
  static restoreTabContent(tabData: TabData): ReactNode {
    return TabService.createTabContent(tabData);
  }

  // Helper method to restore a complete tab from saved data
  static restoreTab(savedTab: TabData): TabData & { content: ReactNode } {
    switch (savedTab.type) {
      case 'flow':
        if (!savedTab.flow) {
          throw new Error('Flow tab requires flow data for restoration');
        }
        return TabService.createFlowTab(savedTab.flow);

      case 'settings':
        return TabService.createSettingsTab();

      case 'sleeves':
        return TabService.createSleevesTab();

      case 'options':
        return TabService.createOptionsTab();

      case 'backtest':
        return TabService.createBacktestTab();

      case 'stocks':
        return TabService.createStocksTab();

      default:
        throw new Error(`Cannot restore unsupported tab type: ${savedTab.type}`);
    }
  }
}