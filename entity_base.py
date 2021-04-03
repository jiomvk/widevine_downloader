# -*- coding: utf-8 -*-
#########################################################
# python
import os, sys, traceback, re, json, threading, time, shutil, subprocess, psutil
from datetime import datetime
# third-party
import requests
# third-party
from flask import request, render_template, jsonify, redirect
# sjva 공용
from framework import db, scheduler, path_data, socketio, SystemModelSetting, app, celery, path_app_root, Util

# 패키지
from .plugin import P
logger = P.logger
package_name = P.package_name
ModelSetting = P.ModelSetting
from .utility import Utility


class EntityDownload:
    pass


class EntityBase(object):
    
    def __init__(self, data):
        self.temp_dir = os.path.join(Utility.tmp_dir, self.name)
        if os.path.exists(self.temp_dir) == False:
            os.makedirs(self.temp_dir)
        self.data = data
        self.code = data['code']
        self.mpd_url = self.mpd = self.mpd_base_url = None
        self.download_list = {'video':[], 'audio':[], 'text':[]}
        self.filepath_mkv = os.path.join(self.temp_dir, '{code}.mkv'.format(code=self.code))
        self.meta = {}

        #self.default_process()
    
    
    def download_start(self):
        try:
            logger.debug(u'공통 처리')
            self.prepare()
            self.find_mpd()
            if self.mpd is not None:
                self.analysis_mpd()
            
            self.make_download_info()
            self.download()
            
            self.clean()
        finally:
            logger.debug("다운로드 종료")



    def find_key(self, kid):
        for key in reversed(self.data['key']):
            if kid == key['kid']:
                return key['key']

    def find_mpd(self):
        logger.debug('Find mpd..')
        if self.mpd_url is None:
            request_list = self.data['har']['log']['entries']
            for item in request_list:
                if item['request']['method'] == 'GET' and item['request']['url'].find('.mpd') != -1:
                    self.mpd_url = item['request']['url']
                    break
        

            
        logger.debug(self.mpd_url)
        from mpegdash.parser import MPEGDASHParser
        self.mpd = MPEGDASHParser.parse(self.mpd_url)
        self.mpd_base_url = self.mpd_url[:self.mpd_url.rfind('/')+1]
        logger.debug(self.mpd_base_url)
        MPEGDASHParser.write(self.mpd, os.path.join(self.temp_dir, '{}.mpd'.format(self.code)))
        logger.debug(u'MPD 저장 ')
                




    def analysis_mpd(self):
        self.adaptation_set = {'video':[], 'audio':[], 'text':[]}
        for period in self.mpd.periods:
            for adaptation_set in period.adaptation_sets:
                item_adaptation_set = {'representation':[]}
                item_adaptation_set['lang'] = adaptation_set.lang
                item_adaptation_set['contentType'] = adaptation_set.content_type
                item_adaptation_set['maxBandwidth']= adaptation_set.max_bandwidth
                
                for representation in adaptation_set.representations:
                    item_representation = {}
                    item_representation['lang'] = adaptation_set.lang
                    item_representation['contentType'] = adaptation_set.content_type

                    item_representation['bandwidth'] = representation.bandwidth
                    item_representation['codecs'] = representation.codecs
                    item_representation['codec_name'] = representation.codecs
                    if item_representation['codecs'].startswith('avc1'):
                        item_representation['codec_name'] = 'H.264'
                    elif item_representation['codecs'].startswith('mp4a.40.2'):
                        item_representation['codec_name'] = 'AAC'
                    

                    item_representation['height'] = representation.height
                    item_representation['width'] = representation.width
                    item_representation['mimeType'] = representation.mime_type 
                    item_representation['url'] = '%s%s' % (self.mpd_base_url, representation.base_urls[0].base_url_value)

                    item_adaptation_set['representation'].append(item_representation)
                self.adaptation_set[item_adaptation_set['contentType']].append(item_adaptation_set)
       




        #logger.debug(json.dumps(self.adaptation_set, indent=4))


    def make_filepath(self, representation):
        #logger.debug(representation)
        if  representation['contentType'] == 'text':
            representation['filepath_download'] = os.path.join(self.temp_dir, '{code}.{lang}.{ext}'.format(code=self.code, lang=representation['lang'], ext=representation['mimeType'].split('/')[1]))
            representation['filepath_merge'] = os.path.join(self.temp_dir, '{code}.{lang}.srt'.format(code=self.code, lang=representation['lang']))
        else:
            representation['filepath_download'] = os.path.join(self.temp_dir, '{code}.{lang}.{bandwidth}.0.mp4'.format(code=self.code, lang=representation['lang'], bandwidth=representation['bandwidth']))
            representation['filepath_merge'] = representation['filepath_download'].replace('.0.mp4', '.mp4')
            representation['filepath_dump'] = representation['filepath_merge'].replace('.mp4', '.mp4.dump')
            representation['filepath_info'] = representation['filepath_merge'].replace('.mp4', '.json')

        #logger.debug(representation)
        return representation


    def download(self):
        try:
            self.merge_option = ['-o', '"%s"' % self.filepath_mkv.replace(path_app_root, '.')]
            self.merge_option_etc = []
            self.audio_codec = ''
            for ct in ['video', 'audio']:
                for item in self.download_list[ct]:
                    logger.debug(item['url'])
                    if os.path.exists(item['filepath_download']) == False:
                        Utility.aria2c_download(item['url'], item['filepath_download'])

                    if os.path.exists(item['filepath_download']) and os.path.exists(item['filepath_dump']) == False:
                        Utility.mp4dump(item['filepath_download'], item['filepath_dump'])

                    logger.debug(os.path.exists(item['filepath_merge']))


                    if os.path.exists(item['filepath_merge']) == False:
                        logger.debug('암호화 해제')
                        text = Utility.read_file(item['filepath_dump'])
                        kid = text.split('default_KID = [')[1].split(']')[0].replace(' ', '')
                        
                        key = self.find_key(kid)
                        logger.debug(self.data['key'])

                        logger.debug('%s:%s', kid, key)
                        Utility.mp4decrypt(item['filepath_download'], item['filepath_merge'], kid, key)
                        logger.debug(os.path.exists(item['filepath_merge']))

                    if os.path.exists(item['filepath_merge']) and os.path.exists(item['filepath_info']) == False:
                        Utility.mp4info(item['filepath_merge'], item['filepath_info'])
                    
                    if ct == 'audio':
                        self.merge_option += ['--language', '0:%s' % item['lang']]
                        self.audio_codec += item['codec_name'] + '.'
                    self.merge_option += ['"%s"' % item['filepath_merge'].replace(path_app_root, '.')]

            for item in self.download_list['text']:
                if os.path.exists(item['filepath_download']) == False:
                    Utility.aria2c_download(item['url'], item['filepath_download'])
                if os.path.exists(item['filepath_download']) and os.path.exists(item['filepath_merge']) == False:
                    if item['mimeType'] == 'text/ttml':
                        Utility.ttml2srt(item['filepath_download'], item['filepath_merge'])
                if item['lang'] == 'ko':
                    self.merge_option += ['--language', '"0:%s"' % item['lang']]
                    self.merge_option += ['--default-track', '"0:yes"']
                    self.merge_option += ['--forced-track', '"0:yes"']
                    self.merge_option += ['"%s"' % item['filepath_merge'].replace(path_app_root, '.')]
                else:
                    self.merge_option_etc += ['--language', '"0:%s"' % item['lang']]
                    self.merge_option += ['"%s"' % item['filepath_merge'].replace(path_app_root, '.')]

            self.output_filename = u'{show_title}.S{season_number}E{episode_number}.{quality}p.{audio_codec}{video_codec}.SfKo.mkv'.format(
                show_title = self.meta['show_title'],
                season_number = str(self.meta['season_number']).zfill(2),
                episode_number = str(self.meta['episode_number']).zfill(2),
                quality = self.download_list['video'][0]['height'],
                audio_codec = self.audio_codec,
                video_codec = self.download_list['video'][0]['codec_name']
            )
            self.filepath_output = os.path.join(Utility.output_dir, self.output_filename)

            if os.path.exists(self.filepath_output) == False:
                Utility.mkvmerge(self.merge_option + self.merge_option_etc)
                shutil.move(self.filepath_mkv, self.filepath_output)
                logger.debug('파일 생성: %s', self.output_filename)

        
        except Exception as e: 
            P.logger.error('Exception:%s', e)
            P.logger.error(traceback.format_exc())

        


    def clean(self):
        for filename in os.listdir(self.temp_dir):
            if filename.startswith(self.code):
                #os.remove(os.path.join(self.temp_dir, filename))
                pass
