'use client';

import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { Asset, Relationship } from '@/app/utils/api';
import { ZoomIn, ZoomOut, Download, Info } from 'lucide-react';

interface LineageVisualizerProps {
  assets: Asset[];
  relationships: Relationship[];
  onNodeClick?: (asset: Asset) => void;
  isLoading?: boolean;
}

interface D3Node extends d3.SimulationNodeDatum {
  id: string;
  asset: Asset;
}

interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  type: string;
}

export default function LineageVisualizer({
  assets = [],
  relationships = [],
  onNodeClick,
  isLoading = false,
}: LineageVisualizerProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [selectedNode, setSelectedNode] = useState<D3Node | null>(null);

  useEffect(() => {
    if (!svgRef.current) {
      return;
    }

    if (assets.length === 0) {
      renderEmptyState();
      return;
    }

    const width = svgRef.current.clientWidth;
    const height = svgRef.current.clientHeight;

    // Clear previous content
    d3.select(svgRef.current).selectAll('*').remove();

    // Create SVG
    const svg = d3
      .select(svgRef.current)
      .attr('width', width)
      .attr('height', height)
      .style('background', '#0f172a');

    // Create main group for zoom/pan
    const g = svg.append('g');

    // Add zoom behavior
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        g.attr('transform', event.transform as any);
      });

    svg.call(zoom as any);

    // Create nodes
    const nodes: D3Node[] = assets.map((asset) => ({
      id: asset.fqn,
      asset,
      x: Math.random() * width,
      y: Math.random() * height,
    }));

    // Create links
    const links: D3Link[] = relationships.map((rel) => ({
      source: nodes.find((n) => n.id === rel.source_fqn) || nodes[0],
      target: nodes.find((n) => n.id === rel.target_fqn) || nodes[0],
      type: rel.relationship_type,
    }));

    // Create simulation
    const simulation = d3
      .forceSimulation(nodes)
      .force(
        'link',
        d3
          .forceLink<D3Node, D3Link>(links)
          .id((d) => d.id)
          .distance(150)
          .strength(0.5)
      )
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(60));

    // Draw links
    const link = g
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('stroke', '#475569')
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0.6);

    // Draw nodes
    const nodeGroup = g
      .selectAll('g.node')
      .data(nodes)
      .join('g')
      .attr('class', 'node')
      .call(
        d3
          .drag<any, D3Node>()
          .on('start', (event, d: D3Node) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (event, d: D3Node) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('end', (event, d: D3Node) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    // Add circles for nodes
    nodeGroup
      .append('circle')
      .attr('r', (d) => getNodeRadius(d.asset.status))
      .attr('fill', (d) => getNodeColor(d.asset.status))
      .attr('stroke', '#1e293b')
      .attr('stroke-width', 2)
      .style('cursor', 'pointer')
      .on('click', (event, d) => {
        setSelectedNode(d);
        onNodeClick?.(d.asset);
      });

    // Add labels
    nodeGroup
      .append('text')
      .text((d) => d.asset.name)
      .style('font-size', '10px')
      .style('fill', 'white')
      .style('text-anchor', 'middle')
      .style('pointer-events', 'none')
      .attr('dy', '0.25em');

    // Add status indicator
    nodeGroup
      .append('circle')
      .attr('r', 4)
      .attr('fill', (d) => getStatusIndicatorColor(d.asset.status))
      .attr('cx', (d) => getNodeRadius(d.asset.status) + 2)
      .attr('cy', (d) => -getNodeRadius(d.asset.status) - 2);

    // Update positions on simulation tick
    simulation.on('tick', () => {
      link
        .attr('x1', (d) => (d.source as D3Node).x || 0)
        .attr('y1', (d) => (d.source as D3Node).y || 0)
        .attr('x2', (d) => (d.target as D3Node).x || 0)
        .attr('y2', (d) => (d.target as D3Node).y || 0);

      nodeGroup.attr('transform', (d) => `translate(${d.x},${d.y})`);
    });

    // Add legend
    renderLegend(svg, width);

    // Reset zoom button
    svg
      .append('text')
      .attr('x', width - 10)
      .attr('y', 20)
      .attr('text-anchor', 'end')
      .style('cursor', 'pointer')
      .style('padding', '5px')
      .style('fill', '#3b82f6')
      .style('font-size', '12px')
      .text('↺ Reset View')
      .on('click', () => {
        svg.transition().duration(750).call(zoom.transform as any, d3.zoomIdentity);
      });

    return () => {
      simulation.stop();
    };
  }, [assets, relationships, onNodeClick]);

  const getNodeColor = (status: string) => {
    const colors: Record<string, string> = {
      breaking: '#ef4444', // red
      failing: '#f97316', // orange
      affected: '#eab308', // yellow
      upstream: '#9ca3af', // gray
    };
    return colors[status] || '#3b82f6';
  };

  const getStatusIndicatorColor = (status: string) => {
    const colors: Record<string, string> = {
      breaking: '#dc2626', // darker red
      failing: '#ea580c', // darker orange
      affected: '#ca8a04', // darker yellow
      upstream: '#6b7280', // darker gray
    };
    return colors[status] || '#1e40af';
  };

  const getNodeRadius = (status: string) => {
    return status === 'breaking' ? 20 : 15;
  };

  const renderEmptyState = () => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const width = svgRef.current.clientWidth || 400;
    const height = svgRef.current.clientHeight || 300;

    svg
      .attr('width', width)
      .attr('height', height)
      .style('background', '#0f172a');

    svg
      .append('text')
      .attr('x', width / 2)
      .attr('y', height / 2)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .style('fill', '#9ca3af')
      .style('font-size', '14px')
      .text('Submit a query to visualize pipeline lineage');
  };

  const renderLegend = (svg: d3.Selection<SVGSVGElement, unknown, null, undefined>, width: number) => {
    const legend = svg.append('g').attr('class', 'legend').attr('transform', 'translate(20, 20)');

    const items = [
      { color: '#ef4444', label: 'Breaking (Failed)' },
      { color: '#f97316', label: 'Failing' },
      { color: '#eab308', label: 'Affected' },
      { color: '#9ca3af', label: 'Upstream' },
    ];

    items.forEach((item, i) => {
      const y = i * 25;
      legend
        .append('circle')
        .attr('cx', 0)
        .attr('cy', y)
        .attr('r', 5)
        .attr('fill', item.color);

      legend
        .append('text')
        .attr('x', 15)
        .attr('y', y)
        .attr('dy', '0.25em')
        .style('fill', '#e2e8f0')
        .style('font-size', '12px')
        .text(item.label);
    });
  };

  return (
    <div className="w-full h-full flex flex-col bg-gray-dark rounded-lg overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold text-white">Data Lineage</h3>
          {isLoading && (
            <div className="w-4 h-4 border-2 border-red-600 border-t-transparent rounded-full animate-spin" />
          )}
        </div>
        <div className="flex gap-2">
          <button
            title="Download as SVG"
            className="p-2 hover:bg-gray-700 rounded transition-colors text-gray-400 hover:text-white"
          >
            <Download className="w-4 h-4" />
          </button>
          <button
            title="Zoom in"
            className="p-2 hover:bg-gray-700 rounded transition-colors text-gray-400 hover:text-white"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            title="Zoom out"
            className="p-2 hover:bg-gray-700 rounded transition-colors text-gray-400 hover:text-white"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Visualization */}
      <div className="flex-1 relative">
        <svg ref={svgRef} className="w-full h-full" />
      </div>

      {/* Info Panel */}
      {selectedNode && (
        <div className="p-4 border-t border-gray-700 bg-gray-800/50">
          <div className="flex items-start gap-3">
            <Info className="w-5 h-5 mt-0.5 text-blue-400 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="font-semibold text-white">{selectedNode.asset.name}</h4>
              <p className="text-xs text-gray-400 mt-1 break-all">{selectedNode.asset.fqn}</p>
              <div className="mt-2 text-xs text-gray-300 space-y-1">
                <p>
                  <span className="text-gray-500">Type:</span> {selectedNode.asset.type}
                </p>
                <p>
                  <span className="text-gray-500">Status:</span>{' '}
                  <span
                    className={`capitalize ${
                      selectedNode.asset.status === 'breaking'
                        ? 'text-red-400'
                        : 'text-gray-400'
                    }`}
                  >
                    {selectedNode.asset.status}
                  </span>
                </p>
                {selectedNode.asset.owner && (
                  <p>
                    <span className="text-gray-500">Owner:</span> {selectedNode.asset.owner}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
