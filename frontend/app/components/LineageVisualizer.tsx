'use client';

import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as d3 from 'd3';
import { Asset, Relationship } from '@/app/utils/api';
import { ZoomIn, ZoomOut, Download, Info, Maximize2 } from 'lucide-react';

interface LineageVisualizerProps {
  assets: Asset[];
  relationships: Relationship[];
  onNodeClick?: (asset: Asset) => void;
  isLoading?: boolean;
}
  
interface D3Node extends d3.SimulationNodeDatum {
  id: string;
  asset: Asset;
  depth?: number;
  layer?: number;
}

interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  type: string;
  sourceNode?: D3Node;
  targetNode?: D3Node;
}

const NODE_COLORS: Record<string, string> = {
  breaking: '#ef4444',
  failing: '#f97316',
  affected: '#eab308',
  upstream: '#64748b',
  default: '#3b82f6',
};

const STATUS_RING: Record<string, string> = {
  breaking: '#fca5a5',
  failing: '#fdba74',
  affected: '#fde047',
  upstream: '#94a3b8',
  default: '#93c5fd',
};

function getNodeColor(status: string) {
  return NODE_COLORS[status] ?? NODE_COLORS.default;
}

function getStatusRing(status: string) {
  return STATUS_RING[status] ?? STATUS_RING.default;
}

function getNodeRadius(status: string) {
  return status === 'breaking' ? 22 : 16;
}

/** Assign depth layers via topological sort (DAG layers) */
function assignLayers(nodes: D3Node[], links: D3Link[]): Map<string, number> {
  const inDegree = new Map<string, number>();
  const adjOut = new Map<string, string[]>();

  nodes.forEach((n) => {
    inDegree.set(n.id, 0);
    adjOut.set(n.id, []);
  });

  links.forEach((l) => {
    const sid = typeof l.source === 'object' ? (l.source as D3Node).id : (l.source as string);
    const tid = typeof l.target === 'object' ? (l.target as D3Node).id : (l.target as string);
    adjOut.get(sid)?.push(tid);
    inDegree.set(tid, (inDegree.get(tid) ?? 0) + 1);
  });

  const layerMap = new Map<string, number>();
  const queue: string[] = [];

  nodes.forEach((n) => {
    if ((inDegree.get(n.id) ?? 0) === 0) {
      queue.push(n.id);
      layerMap.set(n.id, 0);
    }
  });

  while (queue.length > 0) {
    const cur = queue.shift()!;
    const curLayer = layerMap.get(cur) ?? 0;
    for (const next of adjOut.get(cur) ?? []) {
      const existing = layerMap.get(next) ?? 0;
      layerMap.set(next, Math.max(existing, curLayer + 1));
      inDegree.set(next, (inDegree.get(next) ?? 1) - 1);
      if ((inDegree.get(next) ?? 0) === 0) queue.push(next);
    }
  }

  // Assign any unvisited nodes (cycles) to layer 0
  nodes.forEach((n) => {
    if (!layerMap.has(n.id)) layerMap.set(n.id, 0);
  });

  return layerMap;
}

export default function LineageVisualizer({
  assets = [],
  relationships = [],
  onNodeClick,
  isLoading = false,
}: LineageVisualizerProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [selectedNode, setSelectedNode] = useState<D3Node | null>(null);

  const fitView = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return;
    const svg = d3.select(svgRef.current);
    const bounds = (svgRef.current.querySelector('g.scene') as SVGGElement)?.getBBox?.();
    if (!bounds || bounds.width === 0) return;

    const W = svgRef.current.clientWidth;
    const H = svgRef.current.clientHeight;
    const pad = 60;
    const scale = Math.min((W - pad * 2) / bounds.width, (H - pad * 2) / bounds.height, 1.5);
    const tx = W / 2 - scale * (bounds.x + bounds.width / 2);
    const ty = H / 2 - scale * (bounds.y + bounds.height / 2);

    svg
      .transition()
      .duration(600)
      .ease(d3.easeCubicOut)
      .call(zoomRef.current.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }, []);

  const handleZoomIn = () => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 1.4);
  };

  const handleZoomOut = () => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 0.7);
  };

  const handleDownload = () => {
    if (!svgRef.current) return;
    const data = new XMLSerializer().serializeToString(svgRef.current);
    const blob = new Blob([data], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'lineage.svg';
    a.click();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    if (!svgRef.current) return;

    const svgEl = svgRef.current;
    const W = svgEl.clientWidth || 800;
    const H = svgEl.clientHeight || 500;

    d3.select(svgEl).selectAll('*').remove();

    const svg = d3
      .select(svgEl)
      .attr('width', W)
      .attr('height', H)
      .style('background', '#0b1120');

    // ── Defs: arrowhead, glow filter ─────────────────────────────────
    const defs = svg.append('defs');

    defs
      .append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 28)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#475569');

    const glow = defs.append('filter').attr('id', 'glow').attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
    glow.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'coloredBlur');
    const feMerge = glow.append('feMerge');
    feMerge.append('feMergeNode').attr('in', 'coloredBlur');
    feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    // ── Empty state ──────────────────────────────────────────────────
    if (assets.length === 0) {
      svg
        .append('text')
        .attr('x', W / 2)
        .attr('y', H / 2)
        .attr('text-anchor', 'middle')
        .attr('dominant-baseline', 'middle')
        .style('fill', '#334155')
        .style('font-size', '14px')
        .style('font-family', 'monospace')
        .text('Submit a query to visualize pipeline lineage');
      return;
    }

    // ── Build nodes & links ──────────────────────────────────────────
    const nodes: D3Node[] = assets.map((asset) => ({
      id: asset.fqn,
      asset,
    }));

    const nodeById = new Map(nodes.map((n) => [n.id, n]));

    const links: D3Link[] = relationships
      .map((rel) => ({
        source: rel.source_fqn,
        target: rel.target_fqn,
        type: rel.relationship_type,
        sourceNode: nodeById.get(rel.source_fqn),
        targetNode: nodeById.get(rel.target_fqn),
      }))
      .filter((l) => l.sourceNode && l.targetNode);

    // ── Layered layout (Sugiyama-inspired) ──────────────────────────
    const layerMap = assignLayers(nodes, links);
    const maxLayer = Math.max(...Array.from(layerMap.values()), 0);

    // Group nodes per layer
    const layerGroups = new Map<number, D3Node[]>();
    nodes.forEach((n) => {
      const layer = layerMap.get(n.id) ?? 0;
      n.layer = layer;
      if (!layerGroups.has(layer)) layerGroups.set(layer, []);
      layerGroups.get(layer)!.push(n);
    });

    // Spacing constants — generous so nodes never overlap
    const LAYER_GAP = 180;
    const NODE_GAP = 100;

    // Position each node deterministically
    nodes.forEach((n) => {
      const layer = n.layer ?? 0;
      const peers = layerGroups.get(layer)!;
      const idx = peers.indexOf(n);
      const totalH = (peers.length - 1) * NODE_GAP;

      // Lay out LEFT→RIGHT per layer
      n.x = layer * LAYER_GAP + LAYER_GAP / 2;
      n.y = H / 2 - totalH / 2 + idx * NODE_GAP;

      // Fix positions so the simulation only handles edge routing
      n.fx = n.x;
      n.fy = n.y;
    });

    // ── Simulation (edges only — nodes are fixed) ────────────────────
    const simulation = d3
      .forceSimulation(nodes)
      .force(
        'link',
        d3
          .forceLink<D3Node, D3Link>(links)
          .id((d) => d.id)
          .distance(LAYER_GAP)
          .strength(0)
      )
      .force('charge', d3.forceManyBody().strength(0))
      .stop();

    // Run ticks so link positions settle
    simulation.tick(1);

    // ── Main pan/zoom group ──────────────────────────────────────────
    const scene = svg.append('g').attr('class', 'scene');

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.15, 4])
      .on('zoom', (event) => {
        scene.attr('transform', event.transform);
      });

    zoomRef.current = zoom;
    svg.call(zoom);

    // ── Draw edges ───────────────────────────────────────────────────
    const linkGroup = scene.append('g').attr('class', 'links');

    linkGroup
      .selectAll<SVGPathElement, D3Link>('path')
      .data(links)
      .join('path')
      .attr('fill', 'none')
      .attr('stroke', (d) => {
        const src = nodeById.get(typeof d.source === 'string' ? d.source : (d.source as D3Node).id);
        return src ? getNodeColor(src.asset.status) : '#475569';
      })
      .attr('stroke-opacity', 0.35)
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#arrow)')
      .attr('d', (d) => {
        const src = d.source as D3Node;
        const tgt = d.target as D3Node;
        const sx = src.fx ?? src.x ?? 0;
        const sy = src.fy ?? src.y ?? 0;
        const tx = tgt.fx ?? tgt.x ?? 0;
        const ty = tgt.fy ?? tgt.y ?? 0;
        const mx = (sx + tx) / 2;
        // Cubic bezier for smooth curves
        return `M${sx},${sy} C${mx},${sy} ${mx},${ty} ${tx},${ty}`;
      });

    // ── Draw nodes ───────────────────────────────────────────────────
    const nodeGroup = scene
      .append('g')
      .attr('class', 'nodes')
      .selectAll<SVGGElement, D3Node>('g.node')
      .data(nodes)
      .join('g')
      .attr('class', 'node')
      .attr('transform', (d) => `translate(${d.fx ?? d.x},${d.fy ?? d.y})`)
      .style('cursor', 'pointer')
      .call(
        d3
          .drag<SVGGElement, D3Node>()
          .on('start', (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('drag', (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
            d3.select(event.sourceEvent.target.closest('g.node')).attr(
              'transform',
              `translate(${event.x},${event.y})`
            );
            // Redraw edges
            linkGroup.selectAll<SVGPathElement, D3Link>('path').attr('d', (l) => {
              const s = l.source as D3Node;
              const t = l.target as D3Node;
              const sx = s.fx ?? s.x ?? 0;
              const sy = s.fy ?? s.y ?? 0;
              const tx2 = t.fx ?? t.x ?? 0;
              const ty2 = t.fy ?? t.y ?? 0;
              const mx = (sx + tx2) / 2;
              return `M${sx},${sy} C${mx},${sy} ${mx},${ty2} ${tx2},${ty2}`;
            });
          })
      );

    // Outer glow ring for breaking nodes
    nodeGroup
      .filter((d) => d.asset.status === 'breaking')
      .append('circle')
      .attr('r', (d) => getNodeRadius(d.asset.status) + 7)
      .attr('fill', 'none')
      .attr('stroke', '#ef4444')
      .attr('stroke-width', 1.5)
      .attr('stroke-opacity', 0.4)
      .attr('filter', 'url(#glow)');

    // Main circle
    nodeGroup
      .append('circle')
      .attr('r', (d) => getNodeRadius(d.asset.status))
      .attr('fill', (d) => getNodeColor(d.asset.status))
      .attr('fill-opacity', 0.9)
      .attr('stroke', (d) => getStatusRing(d.asset.status))
      .attr('stroke-width', 1.5)
      .on('click', (event, d) => {
        setSelectedNode(d);
        onNodeClick?.(d.asset);
      });

    // Label — placed below the node
    nodeGroup
      .append('text')
      .text((d) => d.asset.name)
      .attr('text-anchor', 'middle')
      .attr('dy', (d) => getNodeRadius(d.asset.status) + 14)
      .style('font-size', '11px')
      .style('font-family', "'JetBrains Mono', 'Fira Code', monospace")
      .style('fill', '#cbd5e1')
      .style('pointer-events', 'none')
      .each(function (d) {
        const el = d3.select(this);
        const text = d.asset.name;
        if (text.length > 18) {
          el.text(text.slice(0, 15) + '…');
        }
      });

    // Type badge — inside circle
    nodeGroup
      .append('text')
      .text((d) => (d.asset.type ?? '').slice(0, 2).toUpperCase())
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .style('font-size', '8px')
      .style('font-weight', '700')
      .style('font-family', 'monospace')
      .style('fill', 'white')
      .style('pointer-events', 'none');

    // ── Layer column headers ─────────────────────────────────────────
    layerGroups.forEach((peers, layer) => {
      const x = layer * LAYER_GAP + LAYER_GAP / 2;
      scene
        .append('text')
        .attr('x', x)
        .attr('y', 18)
        .attr('text-anchor', 'middle')
        .style('fill', '#334155')
        .style('font-size', '10px')
        .style('font-family', 'monospace')
        .style('letter-spacing', '0.1em')
        .text(`LAYER ${layer}`);
    });

    // ── Auto fit-to-view after render ────────────────────────────────
    // Use rAF to let DOM settle first
    requestAnimationFrame(() => {
      const sceneEl = svgEl.querySelector('g.scene') as SVGGElement;
      if (!sceneEl) return;
      const b = sceneEl.getBBox();
      if (!b || b.width === 0) return;
      const pad = 60;
      const scale = Math.min((W - pad * 2) / b.width, (H - pad * 2) / b.height, 1.8);
      const tx = W / 2 - scale * (b.x + b.width / 2);
      const ty = H / 2 - scale * (b.y + b.height / 2);
      svg.call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    });

    return () => {
      simulation.stop();
    };
  }, [assets, relationships, onNodeClick]);

  const LEGEND = [
    { color: NODE_COLORS.breaking, label: 'Breaking' },
    { color: NODE_COLORS.failing, label: 'Failing' },
    { color: NODE_COLORS.affected, label: 'Affected' },
    { color: NODE_COLORS.upstream, label: 'Upstream' },
  ];

  return (
    <div className="w-full h-full flex flex-col bg-[#0b1120] rounded-lg overflow-hidden border border-slate-800">
      {/* ── Header ── */}
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-blue-500" />
          <h3 className="text-sm font-semibold text-slate-100 tracking-wide">Data Lineage</h3>
          {isLoading && (
            <div className="w-3.5 h-3.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          )}
        </div>
        <div className="flex gap-1">
          <button
            title="Fit to view"
            onClick={fitView}
            className="p-1.5 hover:bg-slate-800 rounded text-slate-500 hover:text-slate-200 transition-colors"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
          <button
            title="Zoom in"
            onClick={handleZoomIn}
            className="p-1.5 hover:bg-slate-800 rounded text-slate-500 hover:text-slate-200 transition-colors"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <button
            title="Zoom out"
            onClick={handleZoomOut}
            className="p-1.5 hover:bg-slate-800 rounded text-slate-500 hover:text-slate-200 transition-colors"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <button
            title="Download SVG"
            onClick={handleDownload}
            className="p-1.5 hover:bg-slate-800 rounded text-slate-500 hover:text-slate-200 transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* ── Canvas ── */}
      <div className="flex-1 relative min-h-0">
        <svg ref={svgRef} className="w-full h-full" />

        {/* Legend */}
        <div className="absolute bottom-3 left-3 flex flex-col gap-1.5 bg-slate-900/80 backdrop-blur-sm rounded-md px-3 py-2 border border-slate-800">
          {LEGEND.map(({ color, label }) => (
            <div key={label} className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: color }}
              />
              <span className="text-[10px] text-slate-400 font-mono">{label}</span>
            </div>
          ))}
        </div>

        {/* Hint */}
        <div className="absolute top-2 right-3 text-[10px] text-slate-600 font-mono select-none">
          scroll · drag · click node
        </div>
      </div>

      {/* ── Info Panel ── */}
      {selectedNode && (
        <div className="shrink-0 border-t border-slate-800 bg-slate-900/60 px-4 py-3">
          <div className="flex items-start gap-3">
            <span
              className="mt-0.5 w-2.5 h-2.5 rounded-full shrink-0"
              style={{ backgroundColor: getNodeColor(selectedNode.asset.status) }}
            />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-white">{selectedNode.asset.name}</p>
              <p className="text-[10px] text-slate-500 mt-0.5 break-all font-mono">
                {selectedNode.asset.fqn}
              </p>
              <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-400">
                <span>
                  <span className="text-slate-600">type </span>
                  {selectedNode.asset.type}
                </span>
                <span>
                  <span className="text-slate-600">status </span>
                  <span style={{ color: getNodeColor(selectedNode.asset.status) }}>
                    {selectedNode.asset.status}
                  </span>
                </span>
                {selectedNode.asset.owner && (
                  <span>
                    <span className="text-slate-600">owner </span>
                    {selectedNode.asset.owner}
                  </span>
                )}
              </div>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              className="text-slate-600 hover:text-slate-300 text-xs mt-0.5"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}