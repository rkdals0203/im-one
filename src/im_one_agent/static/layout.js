(function attachLayoutHelpers(root) {
  function calculateResultGridHeight({
    dataPaneHeight,
    tableTopbarHeight,
    queryComposerHeight,
    gridFooterHeight,
    tableScrollHeight,
    minimumSqlHeight = 150,
    minimumResultHeight = 140,
  }) {
    const fixedHeight = tableTopbarHeight + queryComposerHeight + gridFooterHeight;
    const availableForResult = Math.max(
      minimumResultHeight,
      dataPaneHeight - fixedHeight - minimumSqlHeight,
    );
    return Math.ceil(Math.min(tableScrollHeight, availableForResult));
  }

  root.calculateResultGridHeight = calculateResultGridHeight;

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { calculateResultGridHeight };
  }
})(typeof window !== "undefined" ? window : globalThis);
