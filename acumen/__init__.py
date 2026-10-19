"""Acumen: a self-updating market intelligence engine for PSX equities, forex and crypto.

The package has three layers:

* ``acumen.fetch``     collects market data from public sources and keeps a local history store
* ``acumen.analysis``  turns bars and fundamentals into scored, backtested, sized trade ideas
* ``acumen.build``     runs both and writes the JSON files the web app reads

Nothing here is investment advice. Scores, targets and probabilities are model estimates.
"""

__version__ = "1.0.0"
