"""Analysis API; all endpoints require the existing administrator session."""
from __future__ import annotations
from fastapi import Depends, Header, HTTPException, Query
from fastapi.responses import Response
from r20_backend import analysis_service as service
from r20_backend.analysis_store import Archive, sanitize
from r20_backend.analysis_capture import identity
from r20_backend.analysis_metrics import breakdown

def install_routes(app, require_admin):
    def authenticate(x_r20_session: str | None = Header(default=None,alias="X-R20-Session")):
        return require_admin(x_r20_session=x_r20_session)

    def selection(account: str = "", start: str | None = None, end: str | None = None,
                  inst: str = "", side: str = "", config_id: str = "", result: str = "", status: str = "closed"):
        try:
            return account or identity(), service.filters(start,end,inst,side,config_id,result,status)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc

    @app.get("/api/v1/ledger-statistics")
    def visible_ledger_statistics(trade_ids: str = Query("", max_length=8000)):
        from r20_backend.analysis_metrics import summarize, legacy_performance
        ids = set(trade_ids.split(",")) - {""}
        if len(ids) > 100:
            raise HTTPException(status_code=422, detail="最多统计 100 笔可见记录")
        rows = [t for t in Archive().trades(identity()) if t["id"] in ids]
        return {"statistics": summarize(rows), "performance": legacy_performance(rows)}

    prefix = "/api/v1/admin/analysis"
    dependencies = [Depends(authenticate)]

    @app.get(prefix+"/summary",dependencies=dependencies)
    def summary(selected=Depends(selection)):
        return service.load_summary(*selected)

    @app.get(prefix+"/breakdown",dependencies=dependencies)
    def grouped(by: str = "inst", selected=Depends(selection)):
        try:
            account,query=selected
            return {"by":by,"items":breakdown(Archive().trades(account,query),by)}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc

    @app.get(prefix+"/trades",dependencies=dependencies)
    def trades(page: int = Query(1,ge=1),page_size: int = Query(20,ge=1,le=100),selected=Depends(selection)):
        return service.trade_list(*selected,page=page,page_size=page_size)

    @app.get(prefix+"/trades/{trade_id}",dependencies=dependencies)
    def trade(trade_id: str,account: str = ""):
        value=service.trade_detail(account or identity(),trade_id)
        if value is None: raise HTTPException(status_code=404,detail="交易不存在或不属于所选账户")
        return value

    @app.get(prefix+"/events",dependencies=dependencies)
    def events(page: int = Query(1,ge=1),page_size: int = Query(30,ge=1,le=100),selected=Depends(selection)):
        return service.event_list(*selected,page=page,page_size=page_size)

    @app.get(prefix+"/events/{event_id}",dependencies=dependencies)
    def event(event_id: str,account: str = ""):
        value=service.event_detail(account or identity(),event_id)
        if value is None: raise HTTPException(status_code=404,detail="事件不存在或不属于所选账户")
        return value

    @app.get(prefix+"/configurations/{config_id}",dependencies=dependencies)
    def configuration(config_id: str,account: str = ""):
        value=service.configuration_detail(account or identity(),config_id)
        if value is None: raise HTTPException(status_code=404,detail="配置不存在或不属于所选账户")
        return value

    @app.get(prefix+"/export",dependencies=dependencies)
    def export(selected=Depends(selection)):
        account,query=selected
        body=service.export_bundle(account,query)
        return Response(body,media_type="application/zip",
            headers={"Content-Disposition":'attachment; filename="r20-analysis.zip"',"Cache-Control":"private, no-store"})
