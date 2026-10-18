# Static fixture: never imported or executed; all three violations are deliberate.
import typesafe_client as ts

def split_unpinned_uncached_boundary(state, questions):
    ts.ask(state, questions, model='jev-latest')
    ts.ask(state, questions)
