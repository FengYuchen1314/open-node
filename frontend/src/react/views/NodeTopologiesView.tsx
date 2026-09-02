import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "../../ui/icons";
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Modal,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from "../../ui";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";

import type {
  ManagedNodeTopologyCandidate,
  NodeTopology,
  NodeTopologyCandidate,
  NodeTopologyLayout,
  NodeTopologyStage,
} from "../../domain/node-topologies";
import {
  copyTopologyStages,
  insertTopologyCandidate,
  removeTopologyNode,
  removeTopologyStage,
  reorderTopologyStage,
  topologyLayoutForStages,
  validateTopologyDraft,
} from "../../domain/node-topologies";
import {
  createNodeTopology,
  deleteNodeTopology,
  listNodeTopologies,
  nodeTopologyErrorMessage,
  updateNodeTopology,
} from "../../services/node-topologies";
import "./NodeTopologiesView.css";

const candidateMime = "application/x-open-node-topology-candidate";
const stageMime = "application/x-open-node-topology-stage";
const kindLabels: Record<ManagedNodeTopologyCandidate["server_kind"], string> = {
  direct: "直连",
  "leased-line": "专线",
  residential: "住宅",
};
const emptyDraft = () => ({
  name: "", enabled: true, stages: [] as NodeTopologyStage[], layout: {} as NodeTopologyLayout,
});

function date(value: string) { return new Date(value).toLocaleString("zh-CN"); }

export default function NodeTopologiesView() {
  const active = useRef(true);
  const sequence = useRef(0);
  const [topologies, setTopologies] = useState<NodeTopology[]>([]);
  const [candidates, setCandidates] = useState<NodeTopologyCandidate[]>([]);
  const [draft, setDraft] = useState(emptyDraft);
  const [editing, setEditing] = useState<NodeTopology | null>(null);
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [remove, setRemove] = useState<NodeTopology | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const validation = useMemo(
    () => validateTopologyDraft(draft.name, draft.stages, candidates),
    [draft.name, draft.stages, candidates],
  );
  const candidateById = useMemo(() => new Map(candidates.map(item => [item.id, item])), [candidates]);
  const usedNodeIds = useMemo(() => new Set(draft.stages.flatMap(stage => stage.node_ids)), [draft.stages]);
  const usedCandidates = useMemo(
    () => [...usedNodeIds].map(id => candidateById.get(id)).filter((item): item is NodeTopologyCandidate => Boolean(item)),
    [candidateById, usedNodeIds],
  );
  const usedServerIds = useMemo(
    () => new Set(usedCandidates.filter(item => item.kind === "managed").map(item => item.server_id)),
    [usedCandidates],
  );
  const usedExternalOwners = useMemo(
    () => new Set(usedCandidates.filter(item => item.kind === "external").map(item => item.owner_username)),
    [usedCandidates],
  );

  function report(failure: unknown) { setError(nodeTopologyErrorMessage(failure)); }

  async function load() {
    const run = ++sequence.current;
    setLoading(true); setError("");
    try {
      const value = await listNodeTopologies();
      if (!active.current || run !== sequence.current) return;
      setTopologies(value.topologies); setCandidates(value.candidates);
    } catch (failure) { if (active.current && run === sequence.current) report(failure); }
    finally { if (active.current && run === sequence.current) setLoading(false); }
  }

  useEffect(() => {
    active.current = true; void load();
    return () => { active.current = false; sequence.current += 1; };
  }, []);

  function createDraft() {
    setEditing(null); setDraft(emptyDraft()); setSelectedStage(null); setError(""); setNotice("");
  }

  function edit(item: NodeTopology) {
    setEditing(item);
    setDraft({ name: item.name, enabled: item.enabled, stages: copyTopologyStages(item.stages), layout: { ...item.layout } });
    setSelectedStage(item.stages.length ? 0 : null); setError(""); setNotice("");
  }

  function replaceStages(stages: NodeTopologyStage[], nextSelected = selectedStage) {
    setDraft(previous => ({ ...previous, stages, layout: topologyLayoutForStages(previous.layout, stages) }));
    setSelectedStage(stages.length ? Math.min(nextSelected ?? stages.length - 1, stages.length - 1) : null);
    setError(""); setNotice("");
  }

  function add(candidate: NodeTopologyCandidate, stageIndex: number | null) {
    if (stageIndex !== null && stageIndex === draft.stages.length - 1) {
      setError("最终出口必须保持单节点；请把该节点添加为新一跳，或先调整跳数顺序。");
      return;
    }
    const result = insertTopologyCandidate(draft.stages, candidate, candidates, stageIndex);
    if (result.error) { setError(result.error); return; }
    replaceStages(result.stages, stageIndex ?? result.stages.length - 1);
  }

  function removeNode(nodeId: string) {
    replaceStages(removeTopologyNode(draft.stages, nodeId));
  }

  function removeStage(index: number) {
    replaceStages(removeTopologyStage(draft.stages, index), index ? index - 1 : 0);
  }

  function moveStage(from: number, to: number) {
    replaceStages(reorderTopologyStage(draft.stages, from, to), to);
  }

  function dragCandidate(event: DragEvent, candidate: NodeTopologyCandidate) {
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData(candidateMime, candidate.id);
  }

  function dragStage(event: DragEvent, index: number) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(stageMime, String(index));
  }

  function allowDrop(event: DragEvent) { event.preventDefault(); }

  function dropOnStage(event: DragEvent, index: number) {
    event.preventDefault();
    const candidateId = event.dataTransfer.getData(candidateMime);
    if (candidateId) {
      const candidate = candidateById.get(candidateId);
      if (candidate) add(candidate, index);
      else setError("候选节点已经变化，请刷新后重试。");
      return;
    }
    const from = Number(event.dataTransfer.getData(stageMime));
    if (Number.isInteger(from)) moveStage(from, index);
  }

  function dropAsNewStage(event: DragEvent) {
    event.preventDefault();
    const candidate = candidateById.get(event.dataTransfer.getData(candidateMime));
    if (candidate) add(candidate, null);
  }

  async function save() {
    if (busy) return;
    const currentValidation = validateTopologyDraft(draft.name, draft.stages, candidates);
    if (!currentValidation.valid) { setError(currentValidation.errors[0]); return; }
    const payload = {
      name: draft.name.trim(), enabled: draft.enabled, stages: copyTopologyStages(draft.stages),
      layout: topologyLayoutForStages(draft.layout, draft.stages),
    };
    setBusy("save"); setError(""); setNotice("");
    try {
      const saved = editing
        ? await updateNodeTopology(editing.id, { ...payload, expected_revision: editing.revision })
        : await createNodeTopology(payload);
      if (!active.current) return;
      setTopologies(previous => editing
        ? previous.map(item => item.id === saved.id ? saved : item)
        : [...previous, saved]);
      edit(saved);
      setNotice(editing ? "节点编排已更新。" : "节点编排已创建，可在订阅套餐中选择该逻辑节点。");
    } catch (failure) { if (active.current) report(failure); }
    finally { if (active.current) setBusy(""); }
  }

  async function removeCurrent() {
    if (!remove || busy || confirmName !== remove.name) return;
    const target = remove;
    setBusy("delete"); setError(""); setNotice("");
    try {
      await deleteNodeTopology(target.id, { expected_revision: target.revision, confirm_name: confirmName });
      if (!active.current) return;
      setTopologies(previous => previous.filter(item => item.id !== target.id));
      if (editing?.id === target.id) createDraft();
      setRemove(null); setConfirmName(""); setNotice("节点编排已删除。");
    } catch (failure) { if (active.current) { report(failure); void load(); } }
    finally { if (active.current) setBusy(""); }
  }

  const columns = [
    { title: "名称", dataIndex: "name", key: "name", render: (name: string, item: NodeTopology) => <Space><Typography.Text strong>{name}</Typography.Text>{item.enabled ? <Tag color="success">启用</Tag> : <Tag>停用</Tag>}</Space> },
    { title: "路径", key: "path", render: (_: unknown, item: NodeTopology) => `${item.stages.length} 跳 / ${item.stages.reduce((total, stage) => total + stage.node_ids.length, 0)} 节点` },
    { title: "更新时间", dataIndex: "updated_at", key: "updated_at", responsive: ["md" as const], render: date },
    { title: "操作", key: "action", render: (_: unknown, item: NodeTopology) => <Space>
      <Button icon={<EditOutlined />} aria-label={`编辑 ${item.name}`} onClick={() => edit(item)}>编辑</Button>
      <Button danger icon={<DeleteOutlined />} aria-label={`删除 ${item.name}`} onClick={() => { setRemove(item); setConfirmName(""); setError(""); }}>删除</Button>
    </Space> },
  ];

  return <div className="node-topologies-view">
    <Flex className="node-topologies-heading" justify="space-between" align="center" gap={12} wrap>
      <div><Typography.Title level={2}>节点编排</Typography.Title><Typography.Text type="secondary">把托管物理节点与外部订阅节点组合成从左到右的多跳线路，同一跳的多个节点使用轮询负载均衡。</Typography.Text></div>
      <Space><Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={createDraft}>新建编排</Button></Space>
    </Flex>
    <Alert type="info" showIcon title="流向：入口 → 中继 → 最终出口" description="托管节点的同一台服务器只能经过一次；一条编排中的外部节点必须属于同一用户，且仅该用户的主订阅可渲染；最终出口固定为单节点。" />
    {error && <Alert className="node-topologies-feedback" type="error" showIcon title={error} role="alert" closable onClose={() => setError("")} />}
    {notice && <Alert className="node-topologies-feedback" type="success" showIcon title={notice} role="status" closable onClose={() => setNotice("")} />}

    <Card title="已有编排" className="node-topologies-list">
      <Spin spinning={loading}><Table rowKey="id" columns={columns} dataSource={topologies} pagination={false} locale={{ emptyText: <Empty description="尚未创建节点编排" /> }} /></Spin>
    </Card>

    <Card title={editing ? `编辑：${editing.name}` : "创建节点编排"} className="node-topology-editor">
      <div className="node-topology-form-row">
        <label htmlFor="node-topology-name">编排名称</label>
        <Input id="node-topology-name" aria-label="编排名称" maxLength={120} value={draft.name} onChange={event => setDraft(previous => ({ ...previous, name: event.target.value }))} placeholder="例如：香港中继到美国住宅出口" />
        <span className="node-topology-enabled"><Switch aria-label="启用编排" checked={draft.enabled} onChange={enabled => setDraft(previous => ({ ...previous, enabled }))} /> 启用</span>
      </div>

      <div className="node-topology-workspace">
        <section className="node-topology-candidates" aria-labelledby="node-topology-candidate-title">
          <Typography.Title level={4} id="node-topology-candidate-title">候选节点</Typography.Title>
          <Typography.Paragraph type="secondary">拖到右侧空白处创建新一跳，拖到已有跳数列则加入该跳。</Typography.Paragraph>
          <div className="node-topology-candidate-list">
            {candidates.length ? candidates.map(candidate => {
              const used = usedNodeIds.has(candidate.id);
              const reusedServer = !used && candidate.kind === "managed" && usedServerIds.has(candidate.server_id);
              const ownerConflict = !used && candidate.kind === "external" && usedExternalOwners.size > 0
                && (usedExternalOwners.size > 1 || !usedExternalOwners.has(candidate.owner_username));
              const blocked = used || reusedServer || ownerConflict;
              const origin = candidate.kind === "managed"
                ? `${candidate.server_name} · ${candidate.protocol.toUpperCase()}`
                : `来源：${candidate.source_name} · ${candidate.protocol.toUpperCase()}`;
              return <Card key={candidate.id} size="small" draggable={!blocked} data-testid={`candidate-${candidate.id}`} onDragStart={blocked ? undefined : event => dragCandidate(event, candidate)} className="node-topology-candidate-card">
                <Flex justify="space-between" align="start" gap={8}><div><Typography.Text strong>{candidate.name}</Typography.Text><div><Typography.Text type="secondary">{origin}</Typography.Text></div>{candidate.kind === "external" && <div><Typography.Text type="secondary">所属用户：{candidate.owner_username}</Typography.Text></div>}</div><Tag color={candidate.kind === "external" ? "orange" : undefined}>{candidate.kind === "managed" ? kindLabels[candidate.server_kind] : "外部"}</Tag></Flex>
                <Space wrap size={[4, 4]} className="node-topology-candidate-actions">
                  <Button size="small" aria-label={`添加 ${candidate.name} 为新一跳`} disabled={blocked} onClick={() => add(candidate, null)}>新一跳</Button>
                  <Button size="small" aria-label={`加入当前跳 ${candidate.name}`} disabled={blocked || selectedStage === null} onClick={() => selectedStage === null ? setError("请先选择一个跳数列。") : add(candidate, selectedStage)}>加入当前跳</Button>
                  {used && <Tag color="success">已使用</Tag>}{reusedServer && <Tag color="warning">同服务器已经过</Tag>}{ownerConflict && <Tag color="warning">外部节点属于其他用户</Tag>}
                </Space>
              </Card>;
            }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可编排的启用节点" />}
          </div>
        </section>

        <section className="node-topology-canvas" aria-labelledby="node-topology-canvas-title">
          <Flex justify="space-between" align="center" gap={8} wrap><Typography.Title level={4} id="node-topology-canvas-title">有序跳数</Typography.Title><Typography.Text type="secondary">点击列可选中；拖动列可重排</Typography.Text></Flex>
          <div className="node-topology-flow" aria-label="节点编排流向">
            {draft.stages.map((stage, index) => <div className="node-topology-flow-segment" key={`${stage.node_ids.join(":")}:${index}`}>
              {index > 0 && <ArrowRightOutlined className="node-topology-flow-arrow" aria-label="流向下一跳" />}
              <Card
                size="small" draggable data-testid={`topology-stage-${index}`}
                className={`node-topology-stage ${selectedStage === index ? "node-topology-stage-selected" : ""}`}
                onDragStart={event => dragStage(event, index)} onDragOver={allowDrop} onDrop={event => dropOnStage(event, index)}
                onClick={() => setSelectedStage(index)}
                title={<Space><Tag color={index === draft.stages.length - 1 ? "purple" : "blue"}>第 {index + 1} 跳</Tag>{index === draft.stages.length - 1 && <Typography.Text strong>最终出口</Typography.Text>}</Space>}
                extra={<Space size={2}>
                  <Button size="small" type="text" icon={<ArrowLeftOutlined />} aria-label={`前移第 ${index + 1} 跳`} disabled={index === 0} onClick={event => { event.stopPropagation(); moveStage(index, index - 1); }} />
                  <Button size="small" type="text" icon={<ArrowRightOutlined />} aria-label={`后移第 ${index + 1} 跳`} disabled={index === draft.stages.length - 1} onClick={event => { event.stopPropagation(); moveStage(index, index + 1); }} />
                  <Button size="small" danger type="text" icon={<DeleteOutlined />} aria-label={`删除第 ${index + 1} 跳`} onClick={event => { event.stopPropagation(); removeStage(index); }} />
                </Space>}
              >
                {stage.node_ids.length > 1 && <Tag color="cyan" className="node-topology-balance-tag">轮询负载均衡 · {stage.node_ids.length} 节点</Tag>}
                <div className="node-topology-stage-nodes">
                  {stage.node_ids.map(nodeId => {
                    const candidate = candidateById.get(nodeId);
                    const origin = candidate?.kind === "managed"
                      ? `${candidate.server_name} · ${candidate.protocol.toUpperCase()}`
                      : candidate?.kind === "external"
                        ? `来源：${candidate.source_name} · 用户：${candidate.owner_username} · ${candidate.protocol.toUpperCase()}`
                        : nodeId;
                    return <div className="node-topology-stage-node" key={nodeId}><div><Typography.Text strong>{candidate?.name ?? "不可用节点"}</Typography.Text><div><Typography.Text type="secondary">{origin}</Typography.Text></div></div><Button size="small" danger type="text" icon={<DeleteOutlined />} aria-label={`移除 ${candidate?.name ?? nodeId}`} onClick={event => { event.stopPropagation(); removeNode(nodeId); }} /></div>;
                  })}
                </div>
              </Card>
            </div>)}
            <button type="button" className="node-topology-drop-zone" data-testid="topology-new-stage-drop" onDragOver={allowDrop} onDrop={dropAsNewStage} onClick={() => setError("请从左侧拖入节点，或点击候选节点的“新一跳”。")}>
              <PlusOutlined /> 拖入节点新增一跳
            </button>
          </div>
          <div className="node-topology-validation" aria-live="polite">
            {validation.valid
              ? <Alert type="success" showIcon title="线路结构有效：可保存。" />
              : <Alert type="warning" showIcon title="保存前还需处理" description={<ul>{validation.errors.map(message => <li key={message}>{message}</li>)}</ul>} />}
          </div>
        </section>
      </div>
      <Flex justify="end" gap={8} wrap><Button onClick={createDraft}>清空</Button><Button type="primary" loading={busy === "save"} disabled={!validation.valid} onClick={() => void save()}>{editing ? "保存修改" : "创建编排"}</Button></Flex>
    </Card>

    <Modal title="删除节点编排" open={Boolean(remove)} onCancel={() => { if (!busy) { setRemove(null); setConfirmName(""); } }} onOk={() => void removeCurrent()} okText="确认删除" cancelText="取消" okButtonProps={{ danger: true, disabled: !remove || confirmName !== remove.name, loading: busy === "delete" }}>
      <Typography.Paragraph>此操作会删除逻辑节点。请先确认它未被任何订阅套餐使用，然后输入名称 <Typography.Text code>{remove?.name}</Typography.Text>：</Typography.Paragraph>
      <Input aria-label="删除确认名称" value={confirmName} onChange={event => setConfirmName(event.target.value)} autoComplete="off" />
    </Modal>
  </div>;
}
