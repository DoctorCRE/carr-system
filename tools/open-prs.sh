#!/usr/bin/env bash
exec gh api graphql -f query='query {
  search(query: "user:jbookout is:pr is:open", type: ISSUE, first: 100) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      repository { nameWithOwner }
      number title url mergeable mergeStateStatus
    } }
  }
}' --jq '.data.search'
