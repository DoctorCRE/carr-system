// The review gate and WR completion consume the same SHA-bound agent comment grammar.
export function reviewComments(sha, comments) {
  return comments.flatMap(comment => {
    const lines = (comment.body ?? '').split(/\r\n|\n|\r/);
    return lines.length >= 3 && ['VERDICT: APPROVE', 'VERDICT: CHANGES'].includes(lines[0])
      && lines[1] === `Reviewed-SHA: ${sha}` && lines[2].startsWith('Reviewer: ') && lines[2].slice(10).trim()
      ? [{ verdict: lines[0], reviewer: lines[2].slice(10).trim(), url: comment.html_url }] : [];
  });
}

// Agents share a GitHub login; their existing Cards identify independent authors and reviewers.
export function independentApproval(author, sha, comments) {
  const reviews = reviewComments(sha, comments);
  if (reviews.some(review => review.verdict === 'VERDICT: CHANGES')) return null;
  const canonical = card => card.replace(/\d+/g, number => number.replace(/^0+(?=\d)/, ''));
  return reviews.flatMap(review => {
    const cards = [...review.reviewer.matchAll(/\bWR\d+\.S\d+\.J\d+\b/g)].map(match => match[0]);
    return review.verdict === 'VERDICT: APPROVE' && cards.length === 1 && canonical(cards[0]) !== canonical(author)
      ? [{ author_card: author, reviewer_card: cards[0], head_sha: sha, review_url: review.url }] : [];
  }).at(-1) ?? null;
}
