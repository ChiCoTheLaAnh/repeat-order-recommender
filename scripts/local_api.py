"""One historical bundle per process; no event ingestion or on-demand retrieval."""
import argparse
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Query, Request
import uvicorn

from .audit_raw import ROOT
from .release_bundle import BundleValidationError, ReleaseBundle

logger = logging.getLogger('recommendation_requests')


def resolve_bundle(path=None):
    if path:
        return Path(path)
    if os.environ.get('BUNDLE_PATH'):
        return Path(os.environ['BUNDLE_PATH'])
    pointer = ROOT/'outputs/releases/latest.json'
    return pointer.parent/json.loads(pointer.read_text())['release_id']


def create_app(bundle_path=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.bundle,app.state.load_error = None,None
        started = time.perf_counter()
        try:
            app.state.bundle = ReleaseBundle.load(resolve_bundle(bundle_path))
        except (BundleValidationError,OSError,ValueError,KeyError) as error:
            app.state.load_error = type(error).__name__
            logger.error(json.dumps({'event':'bundle_load_failed','error_type':type(error).__name__}))
        app.state.bundle_load_ms = 1000*(time.perf_counter()-started)
        yield

    app = FastAPI(title='Historical repeat-first recommendations',lifespan=lifespan)

    @app.middleware('http')
    async def request_log(request,call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            bundle = getattr(app.state,'bundle',None)
            route = request.scope.get('route')
            logger.info(json.dumps({'event':'request','route':getattr(route,'path','unmatched'),
                'method':request.method,'status':status,'latency_ms':round(1000*(time.perf_counter()-started),3),
                'mode':getattr(request.state,'mode','metadata' if status < 400 else 'error'),
                'version':bundle.manifest['release_id'] if bundle else None}))

    def loaded():
        if app.state.bundle is None:
            raise HTTPException(status_code=503,detail='Historical release bundle unavailable or invalid')
        return app.state.bundle

    @app.get('/health')
    def health():
        return {'status':'alive'}

    @app.get('/ready')
    def ready():
        bundle = loaded()
        return {'status':'ready','release_version':bundle.manifest['release_id'],
                'bundle_load_ms':app.state.bundle_load_ms}

    @app.get('/model')
    def model():
        return loaded().metadata()

    @app.get('/recommendations/{customer_id}')
    def recommendations(customer_id:str,request:Request,k:int=Query(default=10,ge=1,le=20)):
        if set(request.query_params)-{'k'}:
            raise HTTPException(status_code=422,detail='Only the k parameter is supported')
        result = loaded().recommend(customer_id,k)
        request.state.mode = result['recommendation_mode']
        return result

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    uvicorn.run(create_app(args.bundle),host=args.host,port=args.port,workers=1,access_log=False)


if __name__ == '__main__':
    main()
