import command from './command.js';
import ideas from './ideas.js';
import instrument from './instrument.js';
import screener from './screener.js';
import portfolio from './portfolio.js';
import markets from './markets.js';
import evidence from './evidence.js';
import settings from './settings.js';

export const routes = [
  { id: 'home', title: 'Command Center', test: s => s.length === 0, render: command },
  { id: 'ideas', title: 'Trade Ideas', test: s => s[0] === 'ideas', render: ideas },
  { id: 'screener', title: 'Screener', test: s => s[0] === 'screener', render: screener },
  { id: 'instrument', title: 'Instrument', test: s => s[0] === 'i', render: instrument },
  { id: 'portfolio', title: 'Portfolio', test: s => s[0] === 'portfolio', render: portfolio },
  { id: 'markets', title: 'Markets', test: s => s[0] === 'markets', render: markets },
  { id: 'evidence', title: 'Evidence', test: s => s[0] === 'evidence', render: evidence },
  { id: 'settings', title: 'Data & Settings', test: s => s[0] === 'settings', render: settings },
];

export function match(segs) {
  return routes.find(r => r.test(segs)) || routes[0];
}
