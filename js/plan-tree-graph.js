/** @file D3 可缩放方案树导图 */

/**
 * @param {SVGSVGElement} svgEl
 * @param {object[]} treeData
 * @param {{ onNodeClick?: (node: object, pos: { x: number, y: number }) => void }} [opts]
 * @returns {() => void} cleanup
 */
export function renderPlanTreeGraph(svgEl, treeData, opts = {}) {
  if (!svgEl || !window.d3 || !treeData?.length) return () => {};

  const d3 = window.d3;
  const width = svgEl.clientWidth || 480;
  const height = svgEl.clientHeight || 320;

  const rootData = { id: '__root__', title: '方案', children: treeData };
  const root = d3.hierarchy(rootData, (d) => d.children);
  const treeLayout = d3.tree().nodeSize([28, 140]);
  treeLayout(root);

  const nodes = root.descendants().filter((d) => d.data.id !== '__root__');
  const links = root.links().filter((l) => l.source.data.id !== '__root__');

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  nodes.forEach((n) => {
    minX = Math.min(minX, n.x);
    maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y);
    maxY = Math.max(maxY, n.y);
  });
  const pad = 40;
  const contentW = Math.max(maxY - minY, 1);
  const contentH = Math.max(maxX - minX, 1);
  const layoutTx = pad - minY;
  const layoutTy = pad - minX;

  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();
  svg.attr('viewBox', `0 0 ${width} ${height}`);

  const zoomG = svg.append('g').attr('class', 'plan-graph-zoom');
  const layoutG = zoomG
    .append('g')
    .attr('class', 'plan-graph-layout')
    .attr('transform', `translate(${layoutTx}, ${layoutTy})`);

  const fitScale = Math.min(width / (contentW + pad * 2), height / (contentH + pad * 2)) * 0.92;
  const bboxCx = layoutTx + contentW / 2;
  const bboxCy = layoutTy + contentH / 2;
  const initialTransform = d3.zoomIdentity
    .translate(width / 2 - fitScale * bboxCx, height / 2 - fitScale * bboxCy)
    .scale(fitScale);

  const zoom = d3
    .zoom()
    .scaleExtent([fitScale * 0.4, fitScale * 6])
    .on('zoom', (event) => {
      zoomG.attr('transform', event.transform);
    });

  svg.call(zoom);
  svg.call(zoom.transform, initialTransform);
  svg.on('dblclick.zoom', (event) => {
    event.preventDefault();
    svg.transition().duration(250).call(zoom.transform, initialTransform);
  });

  const statusColor = {
    draft: '#94a3b8',
    open: '#3b82f6',
    done: '#22c55e',
  };

  layoutG
    .selectAll('.plan-graph-link')
    .data(links)
    .join('path')
    .attr('class', 'plan-graph-link')
    .attr('fill', 'none')
    .attr('stroke', 'var(--border)')
    .attr('stroke-width', 1.5)
    .attr('d', (d) => {
      const sy = d.source.y;
      const sx = d.source.x;
      const ty = d.target.y;
      const tx = d.target.x;
      return `M${sy},${sx}C${(sy + ty) / 2},${sx} ${(sy + ty) / 2},${tx} ${ty},${tx}`;
    });

  const node = layoutG
    .selectAll('.plan-graph-node')
    .data(nodes)
    .join('g')
    .attr('class', 'plan-graph-node')
    .attr('transform', (d) => `translate(${d.y},${d.x})`)
    .style('cursor', 'pointer')
    .on('click', (event, d) => {
      opts.onNodeClick?.(d.data, { x: event.clientX, y: event.clientY });
    });

  node
    .append('circle')
    .attr('r', 5)
    .attr('fill', (d) => statusColor[d.data.status] || statusColor.open);

  node
    .append('text')
    .attr('dy', '0.32em')
    .attr('x', 10)
    .text((d) => d.data.title || d.data.id || '')
    .attr('class', 'plan-graph-node-label');

  return () => {
    svg.on('.zoom', null);
    svg.on('dblclick.zoom', null);
    svg.selectAll('*').remove();
  };
}
