"""Mocked ELBv2 listener discovery tests; no AWS calls are made here."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.live_topology import discover_live_topology
from app.models import DependencyEdge, ResourceNode


LB_ARN = "arn:aws:elasticloadbalancing:ap-south-1:123:loadbalancer/app/checkout/abc"
TG_ONE = "arn:aws:elasticloadbalancing:ap-south-1:123:targetgroup/checkout-one/tg1"
TG_TWO = "arn:aws:elasticloadbalancing:ap-south-1:123:targetgroup/checkout-two/tg2"


class Paginator:
    def __init__(self, pages: Callable[..., list[dict]] | list[dict]):
        self.pages = pages

    def paginate(self, **kwargs):
        return self.pages(**kwargs) if callable(self.pages) else self.pages


class EmptyClient:
    def get_paginator(self, _method):
        return Paginator([])


class FakeElbv2(EmptyClient):
    def __init__(self, target_groups: list[dict], load_balancers: list[dict], listeners_by_lb: dict[str, list[dict]] | Exception):
        self.target_groups = target_groups
        self.load_balancers = load_balancers
        self.listeners_by_lb = listeners_by_lb

    def get_paginator(self, method):
        if method == "describe_target_groups":
            return Paginator([{"TargetGroups": self.target_groups}])
        if method == "describe_load_balancers":
            return Paginator([{"LoadBalancers": self.load_balancers}])
        if method == "describe_listeners":
            def listeners(**kwargs):
                if isinstance(self.listeners_by_lb, Exception):
                    raise self.listeners_by_lb
                return [{"Listeners": self.listeners_by_lb.get(kwargs["LoadBalancerArn"], [])}]
            return Paginator(listeners)
        return super().get_paginator(method)

    def describe_target_health(self, **_kwargs):
        return {"TargetHealthDescriptions": []}


class FakeSession:
    def __init__(self, elbv2: FakeElbv2):
        self.elbv2 = elbv2

    def client(self, name, **_kwargs):
        if name == "elbv2":
            return self.elbv2
        if name == "sts":
            return type("Sts", (), {"get_caller_identity": lambda _self: {"Account": "123"}})()
        return EmptyClient()


def load_balancer():
    return {"LoadBalancerArn": LB_ARN, "LoadBalancerName": "checkout", "Scheme": "internet-facing", "Type": "application", "VpcId": "vpc-shared"}


def target_group(arn: str):
    return {"TargetGroupArn": arn, "TargetGroupName": arn.split("/")[-2], "VpcId": "vpc-shared"}


def listener(arn_suffix: str, actions: list[dict]):
    return {"ListenerArn": f"arn:aws:elasticloadbalancing:ap-south-1:123:listener/app/checkout/abc/{arn_suffix}", "Port": 443, "Protocol": "HTTPS", "DefaultActions": actions}


def discover(monkeypatch, groups: list[dict], listeners: dict[str, list[dict]] | Exception):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.live_topology._session", lambda: FakeSession(FakeElbv2(groups, [load_balancer()], listeners)))
    db = SessionLocal()
    try:
        result = discover_live_topology(db)
        edges = list(db.scalars(select(DependencyEdge).order_by(DependencyEdge.source_external_id, DependencyEdge.target_external_id)))
        nodes = list(db.scalars(select(ResourceNode)))
        return result, nodes, edges
    finally:
        db.close()


def edge_tuples(edges):
    return {(edge.edge_type, edge.inferred_from, edge.confidence) for edge in edges}


def test_alb_listener_action_links_one_target_group(monkeypatch):
    result, nodes, edges = discover(monkeypatch, [target_group(TG_ONE)], {LB_ARN: [listener("listener-1", [{"Type": "forward", "TargetGroupArn": TG_ONE}])]})

    assert result["warnings"] == []
    assert {node.resource_type for node in nodes} >= {"load_balancer", "listener", "listener_action", "target_group"}
    assert edge_tuples(edges) == {("attached_to_lb", "aws_listener", 1.0), ("attached_to_listener", "aws_listener", 1.0), ("used_by_action", "aws_listener_action", 1.0)}
    assert all(edge.edge_type != "routes_through" for edge in edges)


def test_one_alb_can_forward_one_listener_action_to_multiple_target_groups(monkeypatch):
    _result, _nodes, edges = discover(monkeypatch, [target_group(TG_ONE), target_group(TG_TWO)], {
        LB_ARN: [listener("listener-1", [{"Type": "forward", "ForwardConfig": {"TargetGroups": [{"TargetGroupArn": TG_ONE}, {"TargetGroupArn": TG_TWO}]}}])],
    })

    assert len([edge for edge in edges if edge.edge_type == "used_by_action"]) == 2


def test_listener_without_target_group_is_retained_without_forward_edge(monkeypatch):
    _result, nodes, edges = discover(monkeypatch, [], {LB_ARN: [listener("listener-1", [{"Type": "fixed-response", "FixedResponseConfig": {"StatusCode": "404"}}])]})

    assert any(node.resource_type == "listener_action" for node in nodes)
    assert not [edge for edge in edges if edge.edge_type == "used_by_action"]


def test_multiple_listeners_are_independently_linked(monkeypatch):
    _result, _nodes, edges = discover(monkeypatch, [target_group(TG_ONE), target_group(TG_TWO)], {
        LB_ARN: [
            listener("listener-1", [{"Type": "forward", "TargetGroupArn": TG_ONE}]),
            listener("listener-2", [{"Type": "forward", "TargetGroupArn": TG_TWO}]),
        ],
    })

    assert len([edge for edge in edges if edge.edge_type == "attached_to_lb"]) == 2
    assert len([edge for edge in edges if edge.edge_type == "used_by_action"]) == 2


def test_missing_target_group_does_not_create_a_dangling_relationship(monkeypatch):
    result, _nodes, edges = discover(monkeypatch, [], {LB_ARN: [listener("listener-1", [{"Type": "forward", "TargetGroupArn": TG_ONE}])]})

    assert not [edge for edge in edges if edge.edge_type == "used_by_action"]
    assert any("not returned by DescribeTargetGroups" in warning for warning in result["warnings"])


def test_listener_api_failure_is_reported_without_failing_topology_sync(monkeypatch):
    failure = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DescribeListeners")
    result, nodes, edges = discover(monkeypatch, [target_group(TG_ONE)], failure)

    assert any("ELB listeners skipped" in warning for warning in result["warnings"])
    assert any(node.resource_type == "load_balancer" for node in nodes)
    assert not [edge for edge in edges if edge.edge_type in {"attached_to_lb", "attached_to_listener", "used_by_action"}]
