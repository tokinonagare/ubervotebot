# -*- coding: utf-8 -*-
###############################################################
# ubervotebot is a bot made for Telegram and was written by
# Lars Martens. It helps you manage polls and show the
# results in a variety of formats. This project was built
# ontop of @yukuku's telebot project.
###############################################################

import StringIO
import json
import logging
import random
import math
import urllib
import urllib2

# for sending images
from PIL import Image, ImageDraw, ImageFont
import multipart

# standard app engine imports
from google.appengine.api import urlfetch
from google.appengine.ext import ndb
import webapp2

with open('TOKEN') as f:
    TOKEN = f.read()

BASE_URL = 'https://api.telegram.org/bot' + TOKEN + '/'

# ================================

STATE_DEFAULT = None

STATE_CREATE_POLL_CHOOSE_QUESTION = 'CREATE_POLL_CHOOSE_QUESTION'
STATE_CREATE_POLL_ADD_ANSWER = 'CREATE_POLL_ADD_ANSWER'
STATE_CREATE_POLL_CHOOSE_NUMBER_OF_ANSWERS = 'CREATE_POLL_CHOOSE_NUMBER_OF_ANSWERS'

STATE_DELETE_POLL = 'DELETE_POLL'
STATE_DELETE_POLL_CONFIRM = 'DELETE_POLL_CONFIRM'

STATE_RESULT_CHOOSE_POLL = 'RESULT_CHOOSE_POLL'
STATE_RESULT_CHOOSE_TYPE = 'RESULT_CHOOSE_TYPE'

RESULT_TYPE_LIST = u'显示投票人'
RESULT_TYPE_NUMBERS = u'不含投票人'
RESULT_TYPE_GRID = u'显示投票人的图表'
RESULT_TYPE_BARS = u'不含投票人的图表'

# ================================

class User(ndb.Model):
    id = ndb.IntegerProperty()
    name = ndb.StringProperty()
    surname = ndb.StringProperty()

    activePoll = ndb.StringProperty() # the poll id the user is modifying at the moment
    activeState = ndb.StringProperty() # what operation is the user currently in
    polls = ndb.TextProperty() # stores polls and answers in json: [{...},{...}]
    isProcessing = None  # 用于确认投票的数据修改是否在进行中, 避免并发的产生

    def init(self):
        # load
        if not self.polls:
            self.polls_arr = []
        else:
            self.polls_arr = json.loads(self.polls)

    @classmethod
    def get(cls, user_obj=None, id=None):
        '''user_obj is the telegram user object that will get used for the id, and when a new user is created.
        Use id alternatively'''

        if user_obj:
            u = cls.query().filter(ndb.GenericProperty('id') == user_obj.get('id')).get()
            if not u:
                u = User(name=user_obj.get('first_name'), id=user_obj.get('id'), surname=user_obj.get('surname'))
            u.init()
            return u

        elif id:
            u = cls.query().filter(ndb.GenericProperty('id') == id).get()
            if u:
                u.init()
                return u
        # nothing was found or could be created
        return None

    @classmethod
    def get_username(cls, id):
        """get user name for display on vote view"""
        u = cls.query().filter(ndb.GenericProperty('id') == id).get()

        logging.info('get_username + u: ' + str(u))

        if u:
            return u.get_name()

        return None

    @classmethod
    def create_random_poll_id(cls):
        o = []
        while len(o) < 5:
            c = random.randrange(ord('A'), ord('Z') + 1)
            o.append(chr(c))
        return ''.join(o)

    def create_valid_poll_id(self):
        '''Generates poll ids until we have found a valid one'''
        taken_ids = list(map(lambda x: x.get('id'), self.polls_arr))
        next_id = User.create_random_poll_id()
        while next_id in taken_ids:
            next_id = User.create_random_poll_id()
        return next_id

    # Find an existing poll
    def get_poll(self, id):
        for poll in self.polls_arr:
            if poll.get('id') == id:
                return poll
        return None

    def get_active_poll(self):
        return self.get_poll(self.activePoll)

    def delete_active_poll(self):
        if self.activePoll:
            self.polls_arr.remove(self.get_active_poll())
        self.activePoll = None

    def get_active_poll_answers(self):
        return self.get_active_poll()['answers']

    def get_name(self):
        '''Pretty print name'''
        o = self.name
        if self.surname:
            o += ' ' + self.surname
        return o

    # Create and store a new poll
    def new_poll(self):
        poll = {'id': self.create_valid_poll_id()}
        # Initialize arrays, so we can append stuff later
        poll['answers'] = []
        poll['answered'] = []
        poll['owner'] = self.id
        self.polls_arr.append(poll)
        return poll

    def serialize(self):
        self.polls = json.dumps(self.polls_arr)
        self.put()

    def serialize_by_check(self):
        self.polls = json.dumps(self.polls_arr)
        self.put()

        # 当保存数据成功后, 则可以进行下一次投票
        logging.info('processing check = ' + str(User.isProcessing))
        User.isProcessing = None


# ================================

class MeHandler(webapp2.RequestHandler):
    def get(self):
        urlfetch.set_default_fetch_deadline(60)
        self.response.write(json.dumps(json.load(urllib2.urlopen(BASE_URL + 'getMe'))))


class GetUpdatesHandler(webapp2.RequestHandler):
    def get(self):
        urlfetch.set_default_fetch_deadline(60)
        self.response.write(json.dumps(json.load(urllib2.urlopen(BASE_URL + 'getUpdates'))))


class SetWebhookHandler(webapp2.RequestHandler):
    def get(self):
        urlfetch.set_default_fetch_deadline(60)
        url = self.request.get('url')
        if url:
            self.response.write(json.dumps(json.load(urllib2.urlopen(BASE_URL + 'setWebhook', urllib.urlencode({'url': url})))))

class WebhookHandler(webapp2.RequestHandler):
    def post(self):
        urlfetch.set_default_fetch_deadline(60)
        body = json.loads(self.request.body)
        logging.info('request body:' + str(body))

        self.response.write(json.dumps(body))

        update_id = body['update_id']

        # Return an inline keyboard for a poll
        def get_poll_inline_keyboard(poll, share_button=False):
            keys = '[]'
            if poll['answers']:
                keys = '['

                # iterate over answers
                for i in range(len(poll['answers'])):

                    answer = poll['answers'][i]
                    data = str(poll['owner']) + ';' + str(poll['id']) + ';' + str(i)

                    # Count how often answer at index i was voted for 
                    voted = 0
                    for user_answer in poll['answered']:
                        if user_answer['chosen_answers'] >> i & 1:
                            voted += 1

                    # here hide the amount of the answers, to avoid interfere
                    keys += '[{"text": "'+answer+'", "callback_data": "'+data+'"}],'

                if share_button:
                    keys += '[{"text": "share", "switch_inline_query": "'+poll.get('id')+'"}],'

                # 实名显示投票结果按钮
                show_poll_results_data_display_name = \
                    str(poll['owner']) + ';' + str(poll['id']) + ';' + 'show_poll_results_display_name'
                keys += u'[{"text": "实名显示结果", "callback_data": "'+show_poll_results_data_display_name+'"}],'

                # 匿名显示投票结果按钮
                show_poll_results_data_hide_name = \
                    str(poll['owner']) + ';' + str(poll['id']) + ';' + 'show_poll_results_hide_name'
                keys += u'[{"text": "匿名显示结果", "callback_data": "'+show_poll_results_data_hide_name+'"}],'

                keys = keys[:-1] + ']' # removes the last comma
            return '{"inline_keyboard": '+keys+'}'

        # can know who voted
        def get_poll_status(poll, keyboard_status):

            user_names = []
            for i in range(len(poll['answers'])):

                # Count how often answer at index i was voted for
                for user_answer in poll['answered']:
                    if user_answer['chosen_answers'] >> i & 1:

                        user_name = User.get_username(user_answer['user_id'])
                        if user_name:
                            user_names.append(user_name)

                        logging.info('user_names: ' + str(user_names))

                        # avoid name appear is related to vote select
                        user_names.sort()

            poll_results = '\n' + '\n' + u'已投票的大笨蛋: ' +str(len(user_names)) + '\n' + '\n' + ', '.join(user_names) + '\n'

            # 显示实名的投票结果
            if keyboard_status == 'display_name':

                poll_results += u'\n- 投票结果 -\n'

                for i in range(len(poll['answers'])):
                    names = []
                    for user_answer in poll['answered']:
                        # append user name if he has voted for this answer
                        if user_answer['chosen_answers'] >> i & 1:
                            u = User.get(id=user_answer['user_id'])
                            if u:
                                names.append(u.get_name())

                    poll_results += '\n' + poll['answers'][i] + '\n' + '(' + str(len(names)) + '): ' + ','.join(names)

            # 显示匿名的投票结果
            if keyboard_status == 'hide_name':

                poll_results += u'\n- 投票结果 -\n'

                # count bits for each answer
                for i in range(len(poll['answers'])):
                    count = 0
                    for user_answer in poll['answered']:
                        if user_answer['chosen_answers'] >> i & 1:
                            count += 1

                    poll_results += '\n(' + str(count) + ') ' + poll['answers'][i]

            return poll_results

        def telegram_method(name, keyvalues):

            # encode strings
            encoded = {}
            for key in keyvalues:
                encoded[key] = keyvalues[key].encode('utf-8')

            try:
                logging.info(BASE_URL + str(name) + str(urllib.urlencode(encoded)))

                resp = urllib2.urlopen(BASE_URL + name, urllib.urlencode(encoded)).read()

                logging.info(name+' response:' + resp)

            except Exception, e:
                logging.warn(e)
        
        def send_image(img, chat_id, caption=''):
            resp = multipart.post_multipart(BASE_URL + 'sendPhoto', [
                ('chat_id', str(chat_id)),
                ('caption', caption),
                ('reply_markup', '{"hide_keyboard": true}')
            ], [
                ('photo', 'image.png', img),
            ])
        
        def count_binary_ones(n):
            ones = 0
            # number is 0 -> no bits to check
            if n == 0:
                return 0
            # max number of bits we need to check: int(math.log(n, 2))+1
            for i in range(int(math.log(n, 2))+1):
                if n >> i & 1:
                    ones += 1
            return ones


        # HANDLE INLINE QUERY
        if 'inline_query' in body:
            query = body['inline_query']
            inline_query_id = query['id']

            def send_inline_query_poll_result(poll):
                infos = {
                    'inline_query_id': str(inline_query_id),
                    'switch_pm_text': 'Create new poll',
                    'switch_pm_parameter': 'new'
                }

                if poll:
                    infos['results'] = '[{"type": "article", "id": "'+poll.get('id')+'", "title": "Click here to send poll", "description": "'+poll['question']+'", "thumb_url": "https://raw.githubusercontent.com/haselkern/ubervotebot/master/gfx/botpic.png", "input_message_content": {"message_text": "'+poll['question']+'"}, "reply_markup": '+get_poll_inline_keyboard(poll)+'}]'
                telegram_method('answerInlineQuery', infos)

            # find User
            user = User.get(query['from'])
            user.serialize()
            # find poll
            query_str = query['query']
            poll = user.get_poll(query_str)

            send_inline_query_poll_result(poll)

        # HANDLE CALLBACK_QUERY (from inline keyboards)
        elif 'callback_query' in body:

            # to send an update we need: (message_id and chat_id) or (inline_message_id)
            inline_message_id = None
            try:
                message = body['callback_query']['message']
                message_id = message.get('message_id')
                chat_id = message['chat'].get('id')
            except:
                inline_message_id = body['callback_query'].get('inline_message_id')
            data = body['callback_query']['data']
            user = User.get(body['callback_query']['from'])
            user.serialize()

            # sends a short status that the user will see for a few seconds on the top of the screen
            def ticker(msg):
                telegram_method('answerCallbackQuery', {
                    'callback_query_id': str(body['callback_query']['id']),
                    'text': msg
                })
            
            def update_keyboard(poll, keyboard_status):

                # only show a share button in the chat with the bot
                share_button = not 'inline_message_id' in body['callback_query']
                
                infos = {
                    'text': poll['question'] + get_poll_status(poll, keyboard_status),
                    'reply_markup': get_poll_inline_keyboard(poll, share_button)
                }
                if inline_message_id:
                    infos['inline_message_id'] = inline_message_id
                else:
                    infos['chat_id'] = str(chat_id)
                    infos['message_id'] = str(message_id)
                telegram_method('editMessageText', infos)

            logging.info('post isProcessing = ' + str(User.isProcessing))
            if User.isProcessing:
                ticker(u'投票姬亚历山大, 请稍后再试 (๑•̀ㅂ•́)و✧')
                return
            else:
                User.isProcessing = 'isProcessing'

            data = data.split(';')
            data[0] = int(data[0])
            try:
                # find user the poll belongs to
                poll_owner = User.get(id=data[0])
                # find poll object
                poll = poll_owner.get_poll(data[1])

                if not poll:
                    ticker(u'这个投票已经过期啦~~')
                    return

                # 点击投票结果时, 跟新 投票面板 显示投票结果
                keyboard_status = 'no_results'
                if 'show_poll_results' in data[2]:

                    user_id = body['callback_query']['from']['id']

                    if user_id == data[0]:
                        # update poll display
                        if 'display_name' in data[2]:
                            keyboard_status = 'display_name'
                        elif 'hide_name' in data[2]:
                            keyboard_status = 'hide_name'

                        update_keyboard(poll, keyboard_status)
                    else:
                        ticker(u'又不是你发的投票, 想偷看门儿都没有 (￣３￣)a')

                    User.isProcessing = None

                    return

                # 由于在 show_poll_results 的结果中没有这个数据, 故放到此处处理
                data[2] = int(data[2])

                # get user answer
                user_answer = None
                for ua in poll['answered']:
                    if ua.get('user_id') == user.id:
                        user_answer = ua
                if not user_answer:
                    # append new user
                    user_answer = {'user_id': user.id, 'chosen_answers': 0}
                    poll['answered'].append(user_answer)

                # chosen_answers is an integer where the bits represent if an answer was chosen or not.
                # the rightmost bit represents the answer with index 0

                # old answers
                ua = user_answer['chosen_answers']
                # toggled bit, represents new answers
                ua_next = ua ^ (1 << data[2])

                # too many answers
                if count_binary_ones(ua_next) > poll['max_answers']:
                    # ticker('You cannot select more than ' + str(poll['max_answers']) + ' answers.')
                    ticker(u'只能投 ' + str(poll['max_answers']) + u' 票啦 O(∩_∩)O哈哈~')
                # everything okay, save
                else:
                    # 修改回答的数据
                    user_answer['chosen_answers'] = ua_next

                    # send feedback
                    selected_answer = poll['answers'][data[2]]
                    if ua_next > ua:
                        ticker(u'你的票献给了: ' + selected_answer)
                    else:
                        ticker(u'竟然出尔反尔, 抛弃人家了 (╯﹏╰)')

                # update poll display
                update_keyboard(poll, keyboard_status)

                # save poll
                poll_owner.serialize_by_check()

            except Exception, e:
                # This exception occurs when we send an update that doesn't change the message or its keyboard
                # (or something unforeseen happens)
                logging.exception(e)

        elif 'chosen_inline_result' in body:
            # whatever this is, probably something important
            pass

        # HANDLE MESSAGES AND COMMANDS
        else:
            try:
                message = body['message']
            except:
                logging.error('No message found on body: ' + str(body))
                return

            message_id = message.get('message_id')
            date = message.get('date')
            text = message.get('text')
            fr = message['from']
            chat = message['chat']
            chat_id = chat['id']

            if not text:
                logging.info('no text')
                return

            def reply(msg, keyboard='{"hide_keyboard": true}'):
                telegram_method('sendMessage', {
                    'chat_id': str(chat_id),
                    'text': msg,
                    'disable_web_page_preview': 'true',
                    'reply_markup': keyboard
                })

            def send_action_photo():
                '''Sets status "sending picture" for this bot.'''
                telegram_method('sendChatAction', {
                    'chat_id': str(chat_id),
                    'action' : 'upload_photo'
                })
            
            
            # get User
            user = User.get(fr)
            
            def get_polls_keyboard():
                keys = '['
                for poll in user.polls_arr:
                    s = poll['id'] + ": " + poll['question']
                    keys += '["'+s+'"],'
                keys = keys[:-1] + ']'
                return '{"keyboard": '+keys+', "one_time_keyboard": true, "resize_keyboard": true}'
                

            if user.activeState == STATE_DEFAULT:

                if text == '/start':
                    # show help
                    with open("help.txt", "r") as f:
                        reply(f.read())

                elif text == '/new' or text == '/start new':
                    reply(u'请问投票的主题是什么?')
                    user.activeState = STATE_CREATE_POLL_CHOOSE_QUESTION
                
                elif text == '/delete':
                    if len(user.polls_arr) > 0:
                        reply(u'选择一个投票删除 或者可以 /cancel (取消)', keyboard=get_polls_keyboard())
                        user.activeState = STATE_DELETE_POLL
                    else:
                        reply(u'投票空空如也, 快去发起一个新投票吧 O(∩_∩)O~~')

                elif text == '/results':
                    if len(user.polls_arr) > 0:
                        reply(u'选择一个投票展示结果 或者可以 /cancel (取消)', keyboard=get_polls_keyboard())
                        user.activeState = STATE_RESULT_CHOOSE_POLL
                    else:
                        reply(u'投票空空如也, 快去发起一个新投票吧 O(∩_∩)O~~')
                else:
                    # show help
                    with open('help.txt', 'r') as f:
                        reply(f.read())
            
            elif user.activeState == STATE_RESULT_CHOOSE_POLL:
                if text == '/cancel':
                    user.activeState = STATE_DEFAULT
                    reply(u'已取消结果的展示')
                elif text.startswith('/'):
                    reply(u'请输入一个正确的命令')
                else:
                    poll_id = text[:5]
                    poll = user.get_poll(poll_id)
                    if poll:
                        # Has the poll been answered?
                        if len(poll['answered']) > 0:
                            user.activePoll = poll_id
                            user.activeState = STATE_RESULT_CHOOSE_TYPE

                            reply(u'请选择一个结果的展示方法', keyboard='{"keyboard": [["'+RESULT_TYPE_LIST+'"],["'+RESULT_TYPE_NUMBERS+'"]], "resize_keyboard": true}')
                        else:
                            # No people have answered that poll, no reason for results.
                            user.activePoll = None
                            user.activeState = STATE_DEFAULT
                            reply(u'目前还没有大笨蛋投票呢 ^_^')
                    else:
                        reply(u'请确认投票的ID是正确的')
                        user.activeState = STATE_DEFAULT
            
            elif user.activeState == STATE_RESULT_CHOOSE_TYPE:

                if text == '/cancel':
                    # reply('Okay, no results will be shown.')
                    reply(u'已取消展示结果')
                    user.activeState = STATE_DEFAULT

                elif text == RESULT_TYPE_LIST:
                    # list names of voters
                    poll = user.get_active_poll()

                    msg = poll['question']+u'\n- 投票结果 -\n'

                    for i in range(len(poll['answers'])):
                        names = []
                        for user_answer in poll['answered']:
                            # append user name if he has voted for this answer
                            if user_answer['chosen_answers'] >> i & 1:
                                u = User.get(id=user_answer['user_id'])
                                if u:
                                    names.append(u.get_name())
                        
                        msg += '\n' + poll['answers'][i] + '\n' + '('+str(len(names))+'): ' + ','.join(names)
                    
                    reply(msg)

                else:
                    # just show number of votes
                    poll = user.get_active_poll()
                    msg = poll['question']+'\n- Results -\n'

                    # count bits for each answer
                    for i in range(len(poll['answers'])):
                        count = 0
                        for user_answer in poll['answered']:
                            if user_answer['chosen_answers'] >> i & 1:
                                count += 1
                        msg += '\n('+str(count)+') ' + poll['answers'][i]
                    
                    reply(msg)

                user.activePoll = None
                user.activeState = STATE_DEFAULT
            
            elif user.activeState == STATE_DELETE_POLL:

                if text == '/cancel':
                    user.activeState = STATE_DEFAULT
                    # reply('Nothing was deleted.')
                    reply(u'已取消删除投票的操作')
                else:
                    poll_id = text[:5]
                    poll = user.get_poll(poll_id)
                    if poll:
                        title = poll['question']
                        # reply('Do you really want to delete "'+title+'"?', keyboard='{"keyboard": [["yes", "no"]], "resize_keyboard": true}')
                        reply(u'请确认删除 "'+title+'"?', keyboard='{"keyboard": [["yes", "no"]], "resize_keyboard": true}')
                        user.activePoll = poll_id
                        user.activeState = STATE_DELETE_POLL_CONFIRM
                    else:
                        # reply('No poll with that id was found.')
                        reply(u'没有找到符合ID的投票呢')
                        user.activeState = STATE_DEFAULT
             
            elif user.activeState == STATE_DELETE_POLL_CONFIRM:

                if text == 'yes':
                    poll = user.get_active_poll()
                    title = poll['question']
                    user.delete_active_poll()
                    # reply('Deleted "'+title+'"')
                    reply(u'成功删除 "'+title+'"')
                else:
                    # reply('Nothing was deleted.')
                    reply(u'放弃删除.')

                user.activePoll = None
                user.activeState = STATE_DEFAULT

            elif user.activeState == STATE_CREATE_POLL_CHOOSE_QUESTION:

                if text == '/cancel':
                    user.delete_active_poll()
                    # reply('Cancelled creating a poll.')
                    reply(u'取消创建投票')
                    user.activeState = STATE_DEFAULT
                else:
                    # new poll
                    poll = user.new_poll()
                    poll['question'] = text.replace('"', '\'') # replace " with ' to prevent bad URLs. This is not nice, but it works
                    poll['question'] = poll['question']
                    user.activeState = STATE_CREATE_POLL_ADD_ANSWER
                    user.activePoll = poll['id']
                    # reply('Now send the first answer to that question.')
                    reply(u'请给出第一个选项')

            elif user.activeState == STATE_CREATE_POLL_ADD_ANSWER:

                if text == '/cancel':
                    user.delete_active_poll()
                    # reply('Cancelled creating a poll.')
                    reply(u'取消投票的创建')
                    user.activeState = STATE_DEFAULT

                elif text == '/done':
                    poll = user.get_active_poll()

                    if len(poll['answers']) > 0:
                        # prompt for maximum number of answers a user can select

                        poll = user.get_active_poll()
                        poll['max_answers'] = 1
                        # reply('That\'s it! Your poll is now ready:')
                        reply(u'♪(^∇^*)啦啦 投票创建成功啦, 投票姬正在拼命生成投票ing (๑•̀ㅂ•́)و✧!')

                        # print poll with share button
                        reply(poll['question'], keyboard=get_poll_inline_keyboard(poll, True))

                        user.activeState = STATE_DEFAULT
                        user.activePoll = None

                    else:
                        # users shouldn't send /done without answers
                        # reply('You have to send at least one answer! What should the first answer be?')
                        reply(u'您至少需要提供一个选项哦')
                else:
                    poll = user.get_active_poll()
                    poll['answers'].append(text.replace('"', '\''))  # replace " with ' to prevent bad URLs. This is not nice, but it works
                    # reply('Cool, now send me another answer or type /done when you\'re finished.')
                    reply(u'请输入其他的选项 或者输入 /done (完成) 来结束创建.')

            else:
                # reply('Whoops, I messed up. Please try again.\n(Invalid state: ' + str(user.activeState) + ')')
                reply(u'啊嘞嘞, 投票姬发生了为止的故障 (๑•ᴗ•๑).\n(故障原因: ' + str(user.activeState) + ')')
                user.activeState = STATE_DEFAULT
            
            # save everything
            user.serialize()

# Count users
class CounterHandler(webapp2.RequestHandler):
    def get(self):
        self.response.content_type = "text/plain"
        users = User.query()
        withPoll = 0
        total = 0
        for u in users:
            u.init()
            if len(u.polls_arr) > 0:
                withPoll += 1
            total += 1
        # self.response.write("Total: " + str(total) + "\nWith poll: " + str(withPoll))
        self.response.write(u"总计: " + str(total) + u"\n投票情况: " + str(withPoll))

app = webapp2.WSGIApplication([
    ('/me', MeHandler),
    ('/updates', GetUpdatesHandler),
    ('/set_webhook', SetWebhookHandler),
    ('/webhook', WebhookHandler),
    ('/count', CounterHandler),
], debug=True)
